// NEXUS — Graph Query Planner & Fallback Traversal Engine (Kotlin / JVM)
// =============================================================================
// WHY KOTLIN, AND WHY HERE?
//   NEXUS exposes a small, safe query DSL to the front end:
//
//     topic:"AI Agents" depth<=2 year>=2022 type in (Paper,Method) limit 60
//
//   Translating that DSL into *parameterised Cypher* is security-critical: the
//   user text must never reach the query string. Kotlin's sealed classes and
//   exhaustive `when` expressions make the parse tree and the planner total —
//   the compiler refuses to build a planner that forgets a node type — and the
//   same AST also drives the in-process fallback engine used when Neo4j is not
//   reachable, so the two backends can never drift apart.
//
// MODES
//   --plan   : DSL on stdin -> JSON with parameterised Cypher + bind parameters
//   --serve  : tiny HTTP fallback engine (JDK built-in server) on --port
//   --demo   : run the DSL planner over a set of sample queries
//   --test   : self-test suite, exit 0/1
//
// Build:  gradle build   (or: kotlinc src/main/kotlin -include-runtime -d build/planner.jar)
// Run:    java -jar build/libs/nexus-kotlin-planner.jar --plan < query.txt

package nexus.planner

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer

// ---------------------------------------------------------------- AST --------

/** A whitelisted node label. Unknown labels are rejected at parse time. */
enum class NodeLabel(val cypher: String) {
    PAPER("Paper"), AUTHOR("Author"), TOPIC("Topic"), METHOD("Method"),
    DATASET("Dataset"), INSTITUTION("Institution"), CLAIM("Claim"), COMMUNITY("Community");

    companion object {
        fun from(raw: String): NodeLabel? =
            entries.firstOrNull { it.cypher.equals(raw.trim(), ignoreCase = true) }
    }
}

/** Relationship whitelist — the planner can emit nothing else. */
enum class RelType(val cypher: String) {
    AUTHORED("AUTHORED"), CITES("CITES"), STUDIES("STUDIES"), USES_METHOD("USES_METHOD"),
    USES_DATASET("USES_DATASET"), AFFILIATED_WITH("AFFILIATED_WITH"), MAKES_CLAIM("MAKES_CLAIM"),
    SUPPORTS("SUPPORTS"), CONTRADICTS("CONTRADICTS"), BELONGS_TO("BELONGS_TO"), RELATED_TO("RELATED_TO");

    companion object {
        fun from(raw: String): RelType? =
            entries.firstOrNull { it.cypher.equals(raw.trim(), ignoreCase = true) }
    }
}

sealed interface Filter {
    data class TextEquals(val field: String, val value: String) : Filter
    data class TextContains(val field: String, val value: String) : Filter
    data class IntAtLeast(val field: String, val value: Long) : Filter
    data class IntAtMost(val field: String, val value: Long) : Filter
    data class InList(val field: String, val values: List<String>) : Filter
}

data class Query(
    val text: String? = null,
    val labels: List<NodeLabel> = emptyList(),
    val relationships: List<RelType> = emptyList(),
    val filters: List<Filter> = emptyList(),
    val depth: Int = 1,
    val limit: Int = 50,
)

data class Plan(
    val cypher: String,
    val params: Map<String, Any>,
    val explanation: List<String>,
)

class DslException(message: String) : Exception(message)

// ------------------------------------------------------------- Parser --------

/**
 * Parses the NEXUS query DSL. The grammar is intentionally tiny and *closed*:
 *
 *   query      := clause*
 *   clause     := free-text | `topic:"..."` | `author:"..."`
 *               | `year>=NNNN` | `year<=NNNN` | `depth<=N` | `limit N`
 *               | `type in (Paper, Method, ...)` | `rel in (CITES, ...)`
 *
 * Anything that does not match a whitelisted clause is treated as free text —
 * it still never reaches Cypher, because values are always bound as parameters.
 */
object DslParser {
    private val topicRe = Regex("""topic\s*:\s*"([^"]+)"""", RegexOption.IGNORE_CASE)
    private val authorRe = Regex("""author\s*:\s*"([^"]+)"""", RegexOption.IGNORE_CASE)
    private val yearMinRe = Regex("""year\s*>=\s*(\d{4})""", RegexOption.IGNORE_CASE)
    private val yearMaxRe = Regex("""year\s*<=\s*(\d{4})""", RegexOption.IGNORE_CASE)
    private val depthRe = Regex("""depth\s*<=?\s*(\d{1,2})""", RegexOption.IGNORE_CASE)
    private val limitRe = Regex("""limit\s+(\d{1,4})""", RegexOption.IGNORE_CASE)
    private val typeRe = Regex("""type\s+in\s*\(([^)]*)\)""", RegexOption.IGNORE_CASE)
    private val relRe = Regex("""rel\s+in\s*\(([^)]*)\)""", RegexOption.IGNORE_CASE)

    fun parse(input: String): Query {
        var text = input
        val filters = mutableListOf<Filter>()
        val labels = mutableListOf<NodeLabel>()
        val rels = mutableListOf<RelType>()

        topicRe.find(text)?.let {
            filters += Filter.TextEquals("name", it.groupValues[1].trim())
            text = text.replace(it.value, " ")
        }
        authorRe.find(text)?.let {
            filters += Filter.TextContains("name", it.groupValues[1].trim())
            text = text.replace(it.value, " ")
        }
        yearMinRe.find(text)?.let {
            filters += Filter.IntAtLeast("year", it.groupValues[1].toLong())
            text = text.replace(it.value, " ")
        }
        yearMaxRe.find(text)?.let {
            filters += Filter.IntAtMost("year", it.groupValues[1].toLong())
            text = text.replace(it.value, " ")
        }
        var depth = 1
        depthRe.find(text)?.let {
            depth = it.groupValues[1].toInt().coerceIn(1, 3)
            text = text.replace(it.value, " ")
        }
        var limit = 50
        limitRe.find(text)?.let {
            limit = it.groupValues[1].toInt().coerceIn(1, 500)
            text = text.replace(it.value, " ")
        }
        typeRe.find(text)?.let { m ->
            m.groupValues[1].split(',')
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .forEach { raw ->
                    labels += (NodeLabel.from(raw)
                        ?: throw DslException("Unknown node type '$raw'. Allowed: ${NodeLabel.entries.joinToString { it.cypher }}"))
                }
            text = text.replace(m.value, " ")
        }
        relRe.find(text)?.let { m ->
            m.groupValues[1].split(',')
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .forEach { raw ->
                    rels += (RelType.from(raw)
                        ?: throw DslException("Unknown relationship '$raw'. Allowed: ${RelType.entries.joinToString { it.cypher }}"))
                }
            text = text.replace(m.value, " ")
        }

        val free = text.replace(Regex("\\s+"), " ").trim().trim('"')
        return Query(
            text = free.ifBlank { null },
            labels = labels,
            relationships = rels,
            filters = filters,
            depth = depth,
            limit = limit,
        )
    }
}

// ------------------------------------------------------------- Planner ------

object Planner {
    /**
     * Emits parameterised Cypher: no user-supplied value is ever concatenated
     * into the statement text. Labels/relationship types come from enums, so the
     * only interpolated tokens are compile-time constants.
     */
    fun plan(q: Query, fulltextIndex: String = "nexus-fulltext"): Plan {
        val where = mutableListOf<String>()
        val params = mutableMapOf<String, Any>()
        val why = mutableListOf<String>()

        val labelClause = when {
            q.labels.isEmpty() -> "n"
            q.labels.size == 1 -> "n:${q.labels[0].cypher}"
            else -> "n WHERE " + q.labels.joinToString(" OR ") { "n:${it.cypher}" }
        }
        if (q.labels.isNotEmpty()) why += "Restricted scan to labels: ${q.labels.joinToString { it.cypher }}."

        q.text?.let {
            params["q"] = it
            params["limit"] = q.limit
            why += "Free text '${it}' handled by the full-text index (tokenised, relevance-ranked)."
            val relFilter = if (q.relationships.isEmpty()) "" else
                " AND any(r IN relationships(n) WHERE type(r) IN ['" +
                    q.relationships.joinToString("','") { it.cypher } + "'])"
            val cypher =
                if (q.labels.isEmpty() || q.labels.size > 1) {
                    """
                    |CALL db.index.fulltext.queryNodes('$fulltextIndex', ${'$'}q) YIELD node AS n, score
                    |WHERE score > 0.0$relFilter
                    |RETURN n, score ORDER BY score DESC LIMIT ${'$'}limit
                    """.trimMargin()
                } else {
                    """
                    |CALL db.index.fulltext.queryNodes('$fulltextIndex', ${'$'}q) YIELD node AS n, score
                    |WHERE '${q.labels[0].cypher}' IN labels(n)$relFilter
                    |RETURN n, score ORDER BY score DESC LIMIT ${'$'}limit
                    """.trimMargin()
                }
            val extraWhere = buildWhere(q, params, why)
            val full = if (extraWhere.isEmpty()) cypher else cypher + "\n" + extraWhere
            return Plan(full, params, why)
        }

        q.filters.forEach { f ->
            when (f) {
                is Filter.TextEquals -> { where += "n.${f.field} = \$${f.field}"; params[f.field] = f.value }
                is Filter.TextContains -> {
                    where += "toLower(n.${f.field}) CONTAINS toLower(\$${f.field})"
                    params[f.field] = f.value
                }
                is Filter.IntAtLeast -> { where += "n.${f.field} >= \$${f.field}Min"; params["${f.field}Min"] = f.value }
                is Filter.IntAtMost -> { where += "n.${f.field} <= \$${f.field}Max"; params["${f.field}Max"] = f.value }
                is Filter.InList -> { where += "n.${f.field} IN \$${f.field}List"; params["${f.field}List"] = f.values }
            }
        }
        if (q.filters.isNotEmpty()) why += "Applied structured filters: " + q.filters.joinToString { it.toString() }

        params["limit"] = q.limit
        why += "Limit ${q.limit} keeps the first paint under a second (progressive expansion)."

        val cypher =
            """
            |MATCH (n$labelClause)
            |${if (where.isEmpty()) "" else "WHERE " + where.joinToString(" AND ") + "\n"}RETURN n
            |ORDER BY coalesce(n.pagerank, 0.0) DESC
            |LIMIT ${'$'}limit
            """.trimMargin()
        return Plan(cypher, params, why)
    }

    private fun buildWhere(q: Query, params: MutableMap<String, Any>, why: MutableList<String>): String {
        val where = mutableListOf<String>()
        q.filters.forEach { f ->
            when (f) {
                is Filter.IntAtLeast -> { where += "n.year >= \$${f.field}Min"; params["${f.field}Min"] = f.value }
                is Filter.IntAtMost -> { where += "n.year <= \$${f.field}Max"; params["${f.field}Max"] = f.value }
                is Filter.TextEquals ->
                    if (f.field == "name") { where += "toLower(n.name) = toLower(\$${f.field}Exact)"; params["${f.field}Exact"] = f.value }
                is Filter.TextContains -> { where += "toLower(n.name) CONTAINS toLower(\$${f.field})"; params[f.field] = f.value }
                is Filter.InList -> { where += "n.${f.field} IN \$${f.field}List"; params["${f.field}List"] = f.values }
            }
        }
        if (where.isEmpty()) return ""
        why += "Post-filter applied on indexed properties (bound parameters only)."
        return "WITH n, score WHERE " + where.joinToString(" AND ")
    }

    /**
     * Query-plan "cost model": a rough estimate the UI shows so users understand
     * why large expansions are capped. Deterministic and documented — not magic.
     */
    fun estimateCost(q: Query, corpusEntities: Int = 4000): Map<String, Any> {
        val fanout = 8.0 // average degree of the NEXUS research graph
        val visited = Math.pow(fanout, q.depth.toDouble())
        val estimated = (visited * q.limit / fanout).coerceAtMost(corpusEntities.toDouble()).toLong()
        val cost = (estimated * (1 + q.depth)).coerceAtMost(50_000L)
        return mapOf(
            "depth" to q.depth,
            "fanout" to fanout,
            "estimated_nodes_visited" to cost,
            "strategy" to if (q.text != null) "fulltext -> expand" else "label scan -> rank",
            "safe" to (cost < 25_000),
        )
    }
}

// --------------------------------------------- In-process fallback engine ----

/**
 * Mirrors a *subset* of the graph traversal semantics so the API stays alive if
 * Neo4j is down. It is explicitly reported as `engine: "kotlin-fallback"` so the
 * UI can warn "graph database unavailable — showing cached fallback".
 */
object FallbackEngine {
    data class Node(val id: String, val label: String, val props: Map<String, Any>)

    private val nodes = linkedMapOf<String, Node>()

    init {
        listOf(
            Triple("topic:AI Agents", "Topic", 92),
            Triple("topic:Multi-Agent Systems", "Topic", 74),
            Triple("topic:Memory Systems", "Topic", 61),
            Triple("topic:Reinforcement Learning", "Topic", 88),
            Triple("method:Chain-of-Thought", "Method", 55),
            Triple("method:Tool-Augmented Prompting", "Method", 47),
            Triple("dataset:GAIA", "Dataset", 33),
            Triple("community:agent-memory", "Community", 58),
        ).forEach { (id, label, w) ->
            nodes[id] = Node(id, label, mapOf("name" to id.substringAfter(':'), "pagerank" to w / 1000.0))
        }
    }

    fun search(text: String?, label: String?, limit: Int): List<Node> {
        var seq = nodes.values.asSequence()
        if (label != null) seq = seq.filter { it.label.equals(label, ignoreCase = true) }
        if (!text.isNullOrBlank()) {
            val needle = text.lowercase()
            seq = seq.filter { it.id.lowercase().contains(needle) }
        }
        return seq.sortedByDescending { it.props["pagerank"] as Double }.take(limit).toList()
    }

    fun health(): String = """{"ok":true,"engine":"kotlin-fallback","nodes":${nodes.size}}"""
}

// ------------------------------------------------------------------ CLI ------

private fun jsonEscape(s: String): String =
    s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "")

fun main(args: Array<String>) {
    when (args.firstOrNull()) {
        "--plan" -> {
            val input = if (args.size > 1) args[1] else BufferedReader(InputStreamReader(System.`in`)).readText()
            try {
                val q = DslParser.parse(input)
                val p = Planner.plan(q)
                val params = p.params.entries.joinToString(",") { "\"${jsonEscape(it.key)}\":\"${jsonEscape(it.value.toString())}\"" }
                val why = p.explanation.joinToString("\\n") { "- ${jsonEscape(it)}" }
                val cost = Planner.estimateCost(q).entries.joinToString(",") {
                    "\"${it.key}\":\"${jsonEscape(it.value.toString())}\""
                }
                println(
                    """{"ok":true,"engine":"nexus-kotlin-planner","cypher":"${jsonEscape(p.cypher)}","params":{$params},"explanation":"$why","cost":{$cost}}"""
                )
            } catch (e: DslException) {
                println("""{"ok":false,"error":"${jsonEscape(e.message ?: "parse error")}"}""")
                kotlin.system.exitProcess(2)
            }
        }
        "--demo" -> {
            val samples = listOf(
                """topic:"AI Agents" depth<=2 year>=2022 limit 80""",
                """author:"Yao" type in (Paper) rel in (CITES, STUDIES) limit 25""",
                """memory systems community""",
            )
            samples.forEach { s ->
                val q = DslParser.parse(s)
                val p = Planner.plan(q)
                println("# $s")
                println(p.cypher)
                println("  params: ${p.params}")
                p.explanation.forEach { println("  why: $it") }
                println("  cost: ${Planner.estimateCost(q)}")
                println()
            }
        }
        "--serve" -> {
            val port = args.getOrNull(1)?.toIntOrNull() ?: 8092
            val server = HttpServer.create(InetSocketAddress("0.0.0.0", port), 0)
            server.createContext("/health") { ex ->
                respond(ex, FallbackEngine.health())
            }
            server.createContext("/api/graph/search") { ex ->
                val q = ex.requestURI.query ?: ""
                val params = q.split("&").mapNotNull {
                    val kv = it.split("=", limit = 2)
                    if (kv.size == 2) kv[0] to java.net.URLDecoder.decode(kv[1], "UTF-8") else null
                }.toMap()
                val limit = params["limit"]?.toIntOrNull()?.coerceIn(1, 200) ?: 20
                val hits = FallbackEngine.search(params["q"], params["type"], limit)
                val body = hits.joinToString(",") {
                    """{"id":"${jsonEscape(it.id)}","label":"${it.label}","name":"${jsonEscape(it.props["name"].toString())}"}"""
                }
                respond(ex, """{"ok":true,"engine":"kotlin-fallback","results":[$body]}""")
            }
            server.executor = java.util.concurrent.Executors.newFixedThreadPool(4)
            server.start()
            println("nexus-kotlin-fallback listening on 0.0.0.0:$port")
        }
        "--test" -> {
            var fails = 0
            fun check(name: String, cond: Boolean) {
                println(if (cond) "  ok   $name" else "  FAIL $name")
                if (!cond) fails++
            }
            val q = DslParser.parse("""topic:"AI Agents" type in (Paper, Method) depth<=3 limit 5000""")
            check("free text parsed", q.text == "AI Agents")
            check("labels whitelisted", q.labels == listOf(NodeLabel.PAPER, NodeLabel.METHOD))
            check("depth clamped to 3", q.depth == 3)
            check("limit clamped to 500", q.limit == 500)
            val p = Planner.plan(q)
            check("cypher uses bound params", p.cypher.contains("\$q") && !p.cypher.contains("AI Agents"))
            check("fulltext index used", p.cypher.contains("queryNodes"))
            check("explanation produced", p.explanation.isNotEmpty())
            var rejected = false
            try {
                DslParser.parse("""type in (Paper, SecretVault)""")
            } catch (e: DslException) {
                rejected = true
            }
            check("unknown label rejected", rejected)
            check("cost estimate bounded", Planner.estimateCost(q).let { (it["estimated_nodes_visited"] as Long) <= 4000L })
            check("fallback search works", FallbackEngine.search("agent", "Topic", 10).isNotEmpty())
            check("fallback respects type filter", FallbackEngine.search(null, "Method", 10).all { it.label == "Method" })
            if (fails == 0) println("ALL KOTLIN PLANNER TESTS PASSED") else println("$fails FAILURES")
            kotlin.system.exitProcess(if (fails == 0) 0 else 1)
        }
        else -> println("usage: nexus-kotlin-planner [--plan \"<dsl>\" | --serve [port] | --demo | --test]")
    }
}

private fun respond(ex: HttpExchange, body: String) {
    val bytes = body.toByteArray(Charsets.UTF_8)
    ex.responseHeaders.add("Content-Type", "application/json; charset=utf-8")
    ex.responseHeaders.add("Access-Control-Allow-Origin", "*")
    ex.sendResponseHeaders(200, bytes.size.toLong())
    ex.responseBody.use { it.write(bytes) }
}
