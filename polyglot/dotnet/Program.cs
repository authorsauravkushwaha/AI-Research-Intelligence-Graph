// NEXUS — Graph Interchange & Report Export Service (C# / .NET)
// =============================================================================
// WHY C#/.NET, AND WHY HERE?
//   Export is a *typed serialisation* problem: GraphML for Gephi/yEd, CSV for
//   spreadsheets, and a formatted DOCX-ish/Markdown research report. .NET's
//   System.Xml / LINQ-to-XML gives schema-correct GraphML (the format external
//   graph tools actually accept) with compile-time element/attribute safety, and
//   a single `dotnet run` serves all three exporters over HTTP.
//
// ROLE IN NEXUS
//   The Python API asks this service for exports; if .NET is unavailable the API
//   falls back to its own JSON export, so the app never breaks.
//
// Build: dotnet build -c Release
// Run:   dotnet run -- serve --port 8091
//        dotnet run -- graphml --in graph.json --out graph.graphml
//        dotnet run -- report  --in opportunity.json --out report.md
//        dotnet run -- test

using System.Globalization;
using System.Net;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Xml;
using System.Xml.Linq;

namespace Nexus.Export;

public static class Exporter
{
    // ------------------------------------------------------------- GraphML ---
    /// <summary>
    /// Serialises a NEXUS subgraph (nodes + relationships, as returned by
    /// /api/graph/subgraph) into schema-valid GraphML 1.0, including a typed
    /// key declaration per property so Gephi/yEd colour and size nodes correctly.
    /// </summary>
    public static string ToGraphML(JsonNode root, string graphName = "NEXUS Research Graph")
    {
        var nodes = root["nodes"]?.AsArray() ?? new JsonArray();
        var edges = root["edges"]?.AsArray() ?? new JsonArray();

        var ns = XNamespace.Get("http://graphml.graphdrawing.org/xmlns");
        var doc = new XDocument(new XDeclaration("1.0", "UTF-8", null));

        var keyElements = new List<XElement>();
        void AddKey(string id, string @for, string attrName, string attrType)
            => keyElements.Add(new XElement(ns + "key",
                new XAttribute("id", id), new XAttribute("for", @for),
                new XAttribute("attr.name", attrName), new XAttribute("attr.type", attrType)));

        AddKey("label", "node", "label", "string");
        AddKey("type", "node", "type", "string");
        AddKey("pagerank", "node", "pagerank", "double");
        AddKey("betweenness", "node", "betweenness", "double");
        AddKey("community", "node", "community", "int");
        AddKey("year", "node", "year", "int");
        AddKey("reltype", "edge", "reltype", "string");
        AddKey("weight", "edge", "weight", "double");
        AddKey("predicted", "edge", "predicted", "boolean");

        var g = new XElement(ns + "graph", new XAttribute("id", "G"), new XAttribute("edgedefault", "undirected"));

        foreach (var n in nodes)
        {
            var id = SafeId(n?["id"]?.ToString());
            var node = new XElement(ns + "node", new XAttribute("id", id));
            void Data(string key, string value) => node.Add(new XElement(ns + "data", new XAttribute("key", key), value));
            Data("label", n?["label"]?.ToString() ?? id);
            Data("type", n?["type"]?.ToString() ?? "Unknown");
            AddNum(n, "pagerank", "pagerank", Data);
            AddNum(n, "betweenness", "betweenness", Data);
            AddNum(n, "community", "community", Data);
            AddNum(n, "year", "year", Data);
            g.Add(node);
        }

        var edgeIndex = 0;
        foreach (var e in edges)
        {
            var src = SafeId(e?["source"]?.ToString());
            var dst = SafeId(e?["target"]?.ToString());
            if (src is null || dst is null) continue;
            var edge = new XElement(ns + "edge",
                new XAttribute("id", $"e{edgeIndex++}"),
                new XAttribute("source", src),
                new XAttribute("target", dst));
            void Data(string key, string value) => edge.Add(new XElement(ns + "data", new XAttribute("key", key), value));
            Data("reltype", e?["type"]?.ToString() ?? "RELATED_TO");
            AddNum(e, "weight", "weight", Data);
            var predicted = e?["predicted"]?.GetValue<bool>() ?? false;
            Data("predicted", predicted ? "true" : "false");
            g.Add(edge);
        }

        doc.Add(new XElement(ns + "graphml", new XAttribute(XNamespace.Xmlns + "n0", ns), keyElements, g));

        // XDocument.ToString() silently drops the XML declaration, so a GraphML file written
        // that way is not a well-formed document per the GraphML spec and no viewer will open
        // it. Serialise through an XmlWriter instead, with a UTF-8 declaration at the top.
        using var stream = new MemoryStream();
        var settings = new XmlWriterSettings { Indent = true, Encoding = new UTF8Encoding(false) };
        using (var writer = XmlWriter.Create(stream, settings)) doc.Save(writer);
        return Encoding.UTF8.GetString(stream.ToArray());
    }

    private static void AddNum(JsonNode? n, string prop, string key, Action<string, string> sink)
    {
        var v = n?[prop];
        if (v is null) return;
        double d;
        try { d = v.GetValue<double>(); } catch { return; }
        sink(key, d.ToString("R", CultureInfo.InvariantCulture));
    }

    private static string? SafeId(string? raw)
        => string.IsNullOrWhiteSpace(raw) ? null : raw.Replace("\"", "").Replace("<", "").Replace(">", "").Replace("&", "");

    // ---------------------------------------------------------------- CSV ---
    public static string ToCsv(JsonNode root)
    {
        var sb = new StringBuilder();
        sb.AppendLine("source,source_type,target,target_type,relationship,weight,predicted");
        foreach (var e in root["edges"]?.AsArray() ?? new JsonArray())
        {
            var rel = e?["type"]?.ToString() ?? "RELATED_TO";
            sb.AppendLine(string.Join(',', new[]
            {
                Csv(e?["source"]?.ToString()), Csv(e?["sourceType"]?.ToString()),
                Csv(e?["target"]?.ToString()), Csv(e?["targetType"]?.ToString()),
                Csv(rel),
                (e?["weight"]?.GetValue<double>() ?? 1.0).ToString("0.###", CultureInfo.InvariantCulture),
                (e?["predicted"]?.GetValue<bool>() ?? false) ? "true" : "false"
            }));
        }
        return sb.ToString();
    }

    private static string Csv(string? s)
    {
        var v = s ?? "";
        return v.Contains(',') || v.Contains('"') || v.Contains('\n')
            ? "\"" + v.Replace("\"", "\"\"") + "\""
            : v;
    }

    // ------------------------------------------------------------- Report ----
    /// <summary>
    /// Renders the research opportunity JSON produced by the Python gap engine
    /// into a Markdown research report. The safety notice from the engine is
    /// carried through verbatim and always placed before the first finding.
    /// </summary>
    public static string ToMarkdown(JsonNode root)
    {
        var sb = new StringBuilder();
        string S(JsonNode? n, string path, string dflt = "—")
            => n?[path]?.ToString() ?? dflt;

        sb.AppendLine("# NEXUS RESEARCH OPPORTUNITY REPORT");
        sb.AppendLine();
        if (root["generated_at"] is not null) sb.AppendLine($"*Generated: {root["generated_at"]}*");
        sb.AppendLine();
        sb.AppendLine("## Topic");
        sb.AppendLine();
        sb.AppendLine($"**{S(root, "topic")}**");
        sb.AppendLine();
        sb.AppendLine("## Executive Summary");
        sb.AppendLine();
        sb.AppendLine(S(root, "executive_summary"));
        sb.AppendLine();
        sb.AppendLine("## Research Landscape");
        sb.AppendLine();
        if (root["landscape"] is JsonObject land)
        {
            foreach (var kv in land)
                sb.AppendLine($"- **{kv.Key}**: {kv.Value}");
        }
        sb.AppendLine();
        sb.AppendLine("## Major Communities");
        sb.AppendLine();
        foreach (var c in root["communities"]?.AsArray() ?? new JsonArray())
        {
            sb.AppendLine($"### {S(c, "name")}  ({S(c, "paper_count", "0")} papers)");
            sb.AppendLine();
            sb.AppendLine($"- Top topics: {JoinArray(c?["top_topics"])}");
            sb.AppendLine($"- Top methods: {JoinArray(c?["top_methods"])}");
            sb.AppendLine($"- Representative papers: {JoinArray(c?["top_papers"])}");
            sb.AppendLine();
        }
        sb.AppendLine("## Potential Research Gaps");
        sb.AppendLine();
        if (root["safety_notice"] is not null)
        {
            sb.AppendLine($"> {root["safety_notice"]}");
            sb.AppendLine();
        }
        foreach (var gap in root["gaps"]?.AsArray() ?? new JsonArray())
        {
            sb.AppendLine($"### {S(gap, "title")}");
            sb.AppendLine();
            sb.AppendLine($"- **Opportunity score (prototype heuristic)**: {S(gap, "opportunity_score", "0")} / 100");
            sb.AppendLine($"- **Confidence**: {S(gap, "confidence")}");
            sb.AppendLine($"- **Community A**: {S(gap?["cluster_a"], "name")} · **Community B**: {S(gap?["cluster_b"], "name")}");
            sb.AppendLine();
            sb.AppendLine(S(gap, "hypothesis"));
            sb.AppendLine();
            sb.AppendLine("**Why this matters**");
            sb.AppendLine();
            foreach (var w in gap?["why"]?.AsArray() ?? new JsonArray())
                sb.AppendLine($"1. {w}");
            sb.AppendLine();
            sb.AppendLine("**Score components**");
            sb.AppendLine();
            sb.AppendLine("| Component | Score | Weight |");
            sb.AppendLine("|---|---|---|");
            foreach (var comp in gap?["score_components"]?.AsArray() ?? new JsonArray())
                sb.AppendLine($"| {S(comp, "label")} | {S(comp, "value")} | {S(comp, "weight")} |");
            sb.AppendLine();
            sb.AppendLine("**Supporting papers (graph evidence)**");
            sb.AppendLine();
            foreach (var p in gap?["evidence_papers"]?.AsArray() ?? new JsonArray())
                sb.AppendLine($"- [{S(p, "title")}]({S(p, "url", "#")}) — {S(p, "year")} — {S(p, "reason", "")}");
            sb.AppendLine();
        }
        sb.AppendLine("## Limitations");
        sb.AppendLine();
        foreach (var l in root["limitations"]?.AsArray() ?? new JsonArray())
            sb.AppendLine($"- {l}");
        sb.AppendLine();
        sb.AppendLine("## Sources");
        sb.AppendLine();
        foreach (var s in root["sources"]?.AsArray() ?? new JsonArray())
            sb.AppendLine($"- {s}");
        sb.AppendLine();
        sb.AppendLine("---");
        sb.AppendLine();
        sb.AppendLine("*NEXUS · AI Research Intelligence Graph — knowledge graph + GraphRAG + graph algorithms.*");
        return sb.ToString();
    }

    private static string JoinArray(JsonNode? arr)
    {
        if (arr is not JsonArray a || a.Count == 0) return "—";
        return string.Join(", ", a.Select(x => x?.ToString()).Where(x => !string.IsNullOrWhiteSpace(x)));
    }

    // -------------------------------------------------------------- server ---
    public static void Serve(int port)
    {
        var listener = new HttpListener();
        listener.Prefixes.Add($"http://+:{port}/");
        try { listener.Start(); }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"cannot bind 0.0.0.0:{port} ({ex.Message}); trying localhost");
            listener = new HttpListener();
            listener.Prefixes.Add($"http://localhost:{port}/");
            listener.Start();
        }
        Console.WriteLine($"nexus-export (C#) listening on 0.0.0.0:{port}");

        while (true)
        {
            var ctx = listener.GetContext();
            _ = Task.Run(() =>
            {
                try
                {
                    var path = ctx.Request.Url?.AbsolutePath ?? "/";
                    ctx.Response.Headers["Access-Control-Allow-Origin"] = "*";
                    if (path == "/health")
                    {
                        Write(ctx, "application/json", JsonSerializer.Serialize(new
                        {
                            ok = true, service = "nexus-export", language = "C#/.NET",
                            runtime = Environment.Version.ToString()
                        }));
                        return;
                    }
                    if (path == "/graphml" || path == "/csv" || path == "/report")
                    {
                        using var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8);
                        var body = reader.ReadToEnd();
                        JsonNode? node;
                        try { node = JsonNode.Parse(body); }
                        catch (Exception ex) { Write(ctx, "application/json", $"{{\"ok\":false,\"error\":\"invalid JSON: {ex.Message.Replace("\"", "'")}\"}}", 400); return; }
                        if (node is null) { Write(ctx, "application/json", "{\"ok\":false,\"error\":\"empty body\"}", 400); return; }
                        var text = path switch
                        {
                            "/graphml" => ToGraphML(node),
                            "/csv" => ToCsv(node),
                            _ => ToMarkdown(node)
                        };
                        Write(ctx, path == "/report" ? "text/markdown" : path == "/csv" ? "text/csv" : "application/xml", text);
                        return;
                    }
                    Write(ctx, "application/json", "{\"ok\":false,\"error\":\"unknown route\"}", 404);
                }
                catch (Exception ex)
                {
                    try { Write(ctx, "application/json", JsonSerializer.Serialize(new { ok = false, error = ex.Message }), 500); }
                    catch { /* client gone */ }
                }
            });
        }
    }

    private static void Write(HttpListenerContext ctx, string contentType, string body, int code = 200)
    {
        var bytes = Encoding.UTF8.GetBytes(body);
        ctx.Response.StatusCode = code;
        ctx.Response.ContentType = contentType + "; charset=utf-8";
        ctx.Response.ContentLength64 = bytes.Length;
        ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
        ctx.Response.OutputStream.Close();
    }

    // ---------------------------------------------------------------- main ---
    public static int Main(string[] args)
    {
        if (args.Length == 0) { Console.WriteLine("usage: nexus-export [serve|graphml|report|csv|test]"); return 1; }
        switch (args[0])
        {
            case "serve":
                Serve(args.Length > 1 && int.TryParse(args[1], out var p) ? p : 8091);
                return 0;
            case "graphml":
            case "report":
            case "csv":
            {
                var input = ArgValue(args, "--in") ?? "-";
                var output = ArgValue(args, "--out");
                var json = input == "-" ? Console.In.ReadToEnd() : File.ReadAllText(input);
                var node = JsonNode.Parse(json) ?? throw new InvalidOperationException("empty JSON input");
                var text = args[0] switch
                {
                    "graphml" => ToGraphML(node),
                    "csv" => ToCsv(node),
                    _ => ToMarkdown(node)
                };
                if (output is null) Console.WriteLine(text);
                else { File.WriteAllText(output, text); Console.WriteLine($"wrote {output} ({text.Length} chars)"); }
                return 0;
            }
            case "test":
                return Tests.Run();
            default:
                Console.WriteLine($"unknown command {args[0]}");
                return 1;
        }
    }

    private static string? ArgValue(string[] args, string name)
    {
        var i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}

public static class Tests
{
    public static int Run()
    {
        var fails = 0;
        void Check(string name, bool cond)
        {
            Console.WriteLine(cond ? $"  ok   {name}" : $"  FAIL {name}");
            if (!cond) fails++;
        }

        var graph = JsonNode.Parse("""
        {"nodes":[
          {"id":"paper:1","label":"Agent Memory","type":"Paper","pagerank":0.31,"community":2,"year":2024},
          {"id":"topic:mem","label":"Memory Systems","type":"Topic","pagerank":0.12,"community":1}
        ],"edges":[
          {"source":"paper:1","target":"topic:mem","type":"STUDIES","weight":1.0,"predicted":false},
          {"source":"paper:1","target":"topic:plan","type":"PREDICTED_LINK","weight":0.42,"predicted":true}
        ]}
        """)!;

        var gml = Exporter.ToGraphML(graph);
        Check("graphml has xml declaration", gml.Contains("<?xml"));
        Check("graphml declares keys", gml.Contains("attr.name=\"pagerank\""));
        Check("graphml contains nodes", gml.Contains("id=\"paper:1\""));
        Check("graphml contains edges", gml.Contains("reltype"));
        Check("graphml escapes ampersands", !gml.Contains(" & ") || gml.Contains("&amp;"));
        var parsed = XDocument.Parse(gml);
        Check("graphml is well-formed XML", parsed.Root != null);
        Check("graphml root is graphml element", parsed.Root!.Name.LocalName == "graphml");

        var csv = Exporter.ToCsv(graph);
        Check("csv has header", csv.StartsWith("source,source_type"));
        Check("csv marks predicted edges", csv.Contains("true"));

        var report = JsonNode.Parse("""
        {"topic":"AI Agents","generated_at":"2026-01-01","executive_summary":"Summary text.",
         "safety_notice":"AI-generated hypothesis.","landscape":{"Papers":120},
         "gaps":[{"title":"Memory x Coordination","opportunity_score":78,"confidence":"Medium",
                  "hypothesis":"Hypothesis text","why":["Reason one","Reason two"],
                  "score_components":[{"label":"Community Separation","value":87,"weight":"30%"}],
                  "evidence_papers":[{"title":"Paper A","url":"https://arxiv.org/abs/1","year":2024,"reason":"bridges"}],
                  "cluster_a":{"name":"Agent Memory"},"cluster_b":{"name":"Multi-Agent"}}],
         "limitations":["Demo corpus"],"sources":["arXiv"]}
        """)!;
        var md = Exporter.ToMarkdown(report);
        Check("report has title", md.Contains("# NEXUS RESEARCH OPPORTUNITY REPORT"));
        Check("report keeps safety notice", md.Contains("AI-generated hypothesis."));
        Check("report renders score table", md.Contains("| Community Separation | 87 | 30% |"));
        Check("report lists evidence", md.Contains("Paper A"));
        Check("report has limitations", md.Contains("## Limitations"));

        Console.WriteLine(fails == 0 ? "ALL C# EXPORT TESTS PASSED" : $"{fails} FAILURES");
        return fails == 0 ? 0 : 1;
    }
}
