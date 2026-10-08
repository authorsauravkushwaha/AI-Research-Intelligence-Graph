// NEXUS — Ingestion & Embedding Service (Go)
// =============================================================================
// WHY GO, AND WHY HERE?
//   Ingestion is a *streaming, concurrent, network-bound* job: fetch metadata for
//   hundreds of papers, extract entities, chunk abstracts, produce embeddings.
//   Go's goroutines + context cancellation make bounded-concurrency fan-out
//   trivial and predictable, and the result is a single static binary that a
//   judge can run with no runtime installed.
//
// TWO ROLES
//   1. Extraction: deterministic, dictionary+rule based entity extraction
//      (topics, methods, datasets, institutions, metrics) with provenance
//      offsets. Deterministic extraction means the demo corpus does not shift
//      between runs — important for reproducibility.
//   2. Embeddings: exposes an OpenAI-compatible POST /v1/embeddings. If
//      NEXUS_EMBEDDINGS_URL is set, Go *proxies* to the real embedding provider
//      (so vector search uses genuine semantic vectors); otherwise it serves a
//      reproducible hashed-feature embedding (documented as a lexical baseline,
//      NOT a semantic model) so the pipeline runs offline.
//
// Build:  go build -o ../../bin/nexus-ingest ./...
// Run:    NEXUS_INGEST_PORT=8090 ./nexus-ingest serve
//         ./nexus-ingest extract -corpus ../../data/demo/corpus.json -out ../../data/demo/extracted.json
//         ./nexus-ingest test

package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const Version = "1.0.0"

// ------------------------------------------------------------------ types ---

// Paper accepts both shapes NEXUS uses: the ingestion service's own wire format (`id`,
// `abstract`) and a `data/demo/corpus.json` record (`arxiv_id`, `summary`, curated
// `topics`/`methods`/`datasets`). `text()` and `key()` collapse the difference.
type Paper struct {
	ID            string   `json:"id"`
	ArXivID       string   `json:"arxiv_id"`
	Title         string   `json:"title"`
	Abstract      string   `json:"abstract"`
	Summary       string   `json:"summary"`
	Year          int      `json:"year"`
	Venue         string   `json:"venue"`
	Field         string   `json:"field"`
	URL           string   `json:"url"`
	CitationCount int      `json:"citation_count"`
	Authors       []string `json:"authors"`
	Institutions  []string `json:"institutions"`
	CurationTier  string   `json:"corpus_tier"`
}

// text is the prose entity extraction runs over: the wire `abstract` when the caller has
// one, otherwise the corpus record's editorial `summary`.
func (p Paper) text() string {
	if strings.TrimSpace(p.Abstract) != "" {
		return p.Abstract
	}
	return p.Summary
}

// key is the identifier used in provenance: the ingestion id, or the arXiv id of a corpus
// record, so an extraction traces back to the record it came from.
func (p Paper) key() string {
	if p.ID != "" {
		return p.ID
	}
	return p.ArXivID
}

type Extraction struct {
	PaperID    string       `json:"paper_id"`
	Topics     []string     `json:"topics"`
	Methods    []string     `json:"methods"`
	Datasets   []string     `json:"datasets"`
	Metrics    []string     `json:"metrics"`
	Claims     []Claim      `json:"claims"`
	Provenance []Provenance `json:"provenance"`
}

type Claim struct {
	Text   string `json:"text"`
	Stance string `json:"stance"` // supports | contradicts | reports
}

type Provenance struct {
	Entity string `json:"entity"`
	Kind   string `json:"kind"`
	Field  string `json:"field"` // title | abstract
	Offset int    `json:"offset"`
}

type EmbeddingRequest struct {
	Input []string `json:"input"`
	Model string   `json:"model"`
}

type EmbeddingResponse struct {
	Object string          `json:"object"`
	Model  string          `json:"model"`
	Data   []EmbeddingItem `json:"data"`
	Engine string          `json:"engine"`
	Note   string          `json:"note,omitempty"`
}

type EmbeddingItem struct {
	Object    string    `json:"object"`
	Index     int       `json:"index"`
	Embedding []float64 `json:"embedding"`
}

// Dictionary-driven extraction. Dictionaries are small on purpose: a hackathon
// demo must be auditable, and every dictionary hit is stored with provenance so
// a researcher can trace an entity back to the exact sentence.
var topicDict = []string{
	"ai agents", "multi-agent systems", "agent memory", "memory systems", "planning",
	"reasoning", "tool use", "reinforcement learning", "retrieval augmented generation",
	"retrieval", "robotics", "computer vision", "generative ai", "climate ai",
	"quantum computing", "cybersecurity", "healthcare ai", "llm agents", "alignment",
	"evaluation", "simulation", "swarm intelligence", "human-ai collaboration",
	"world models", "continual learning", "code generation", "scientific discovery",
}

// topicAliases maps an unambiguous surface mention onto the canonical topic the rest of
// NEXUS stores, so a paper that says "persistent memory" or "long-term memory" is connected
// to Memory Systems instead of being left with only its incidental mentions. Only multi-word
// forms are listed — a bare "memory" or "agents" would over-match, and every alias is
// recorded with the offset of the mention that triggered it.
var topicAliases = []struct {
	Mention   string
	Canonical string
}{
	{"long-term memory", "memory systems"},
	{"long term memory", "memory systems"},
	{"persistent memory", "memory systems"},
	{"episodic memory", "memory systems"},
	{"working memory", "memory systems"},
	{"multi agent", "multi-agent systems"},
	{"multi-agent", "multi-agent systems"},
}

var methodDict = []string{
	"chain-of-thought", "tree of thoughts", "react", "reflexion", "self-consistency",
	"rainbow", "mappo", "ippo", "q-learning", "ppo", "actor-critic", "transformers",
	"graph neural networks", "lora", "rlhf", "dpo", "in-context learning",
	"retrieval-augmented generation", "vector search", "knowledge graphs", "monte carlo tree search",
	"bayesian optimization", "diffusion models", "mixture of experts", "multi-agent reinforcement learning",
}

var datasetDict = []string{
	"gaia", "webshop", "alfworld", "hotpotqa", "swe-bench", "humaneval", "agentbench",
	"mujoco", "atari", "openai gym", "commonsenseqa", "mbpp", "alfred", "habitat",
	"the stack", "natural questions", "triviaqa", "math", "gsm8k", "toolbench",
}

var metricDict = []string{
	"accuracy", "f1", "success rate", "pass@1", "reward", "sample efficiency",
	"pass rate", "win rate", "throughput", "latency", "cost", "tokens", "hallucination rate",
}

var stancePatterns = []struct {
	re     *regexp.Regexp
	stance string
}{
	{regexp.MustCompile(`(?i)\b(we (show|demonstrate|find)|results (show|demonstrate|indicate)|outperforms?|improves?|enables?|achieves?)\b`), "supports"},
	{regexp.MustCompile(`(?i)\b(fails? to|does not|do not|cannot|degrades?|underperforms?|hurts?|worse than|limitation)\b`), "contradicts"},
	{regexp.MustCompile(`(?i)\b(we study|we analyze|we measure|we report|we evaluate)\b`), "reports"},
}

// ------------------------------------------------------------- extraction ---

func phraseHits(text string, dict []string) []struct {
	Phrase string
	Offset int
} {
	lower := strings.ToLower(text)
	var out []struct {
		Phrase string
		Offset int
	}
	for _, p := range dict {
		idx := strings.Index(lower, p)
		if idx >= 0 {
			out = append(out, struct {
				Phrase string
				Offset int
			}{p, idx})
		}
	}
	return out
}

// acronyms keeps the entity names the rest of NEXUS already uses (the graph topics and
// methods the Python ingestion and the demo corpus store) instead of inventing a spelling.
var acronyms = map[string]string{
	"ai": "AI", "llm": "LLM", "llms": "LLMs", "nlp": "NLP", "rag": "RAG", "mcp": "MCP",
	"gaia": "GAIA", "react": "ReAct", "rlhf": "RLHF", "dpo": "DPO", "ppo": "PPO",
	"mappo": "MAPPO", "ippo": "IPPO", "lora": "LoRA", "webshop": "WebShop",
	"alfworld": "ALFWorld", "humaneval": "HumanEval", "swe-bench": "SWE-bench",
	"hotpotqa": "HotpotQA", "agentbench": "AgentBench", "q-learning": "Q-Learning",
	"pagerank": "PageRank", "graphrag": "GraphRAG",
}

// stopWords stay lowercase inside a hyphenated phrase ("tree of thoughts",
// "chain-of-thought"), which is how the same entities are named everywhere else.
var stopWords = map[string]bool{
	"of": true, "the": true, "and": true, "for": true, "with": true, "in": true,
	"to": true, "on": true, "a": true, "an": true, "via": true, "per": true,
}

// topicHits = dictionary hits plus the canonicalising aliases above.
func topicHits(text string) []struct {
	Phrase string
	Offset int
} {
	hits := phraseHits(text, topicDict)
	lower := strings.ToLower(text)
	for _, alias := range topicAliases {
		idx := strings.Index(lower, alias.Mention)
		if idx < 0 {
			continue
		}
		already := false
		for _, hit := range hits {
			if hit.Phrase == alias.Canonical {
				already = true
				break
			}
		}
		if !already {
			hits = append(hits, struct {
				Phrase string
				Offset int
			}{alias.Canonical, idx})
		}
	}
	return hits
}

func titleCasePhrase(p string) string {
	words := strings.Split(p, " ")
	for i, w := range words {
		words[i] = titleCaseWord(w, i == 0)
	}
	return strings.Join(words, " ")
}

// titleCaseWord capitalises each hyphen/slash-separated segment ("multi-agent systems" →
// "Multi-Agent Systems"), keeps known acronyms exact, and leaves internal stop words alone
// ("tree of thoughts" → "Tree of Thoughts", "chain-of-thought" → "Chain-of-Thought").
func titleCaseWord(w string, first bool) string {
	if w == "" {
		return w
	}
	if v, ok := acronyms[strings.ToLower(w)]; ok {
		return v
	}
	segs := strings.FieldsFunc(w, func(r rune) bool { return r == '-' || r == '/' })
	if len(segs) > 1 {
		sep := "-"
		if strings.Contains(w, "/") {
			sep = "/"
		}
		for i, seg := range segs {
			lower := strings.ToLower(seg)
			if v, ok := acronyms[lower]; ok {
				segs[i] = v
				continue
			}
			if i > 0 && stopWords[lower] {
				segs[i] = lower
				continue
			}
			segs[i] = strings.ToUpper(lower[:1]) + lower[1:]
		}
		return strings.Join(segs, sep)
	}
	lower := strings.ToLower(w)
	if !first && stopWords[lower] {
		return lower
	}
	return strings.ToUpper(lower[:1]) + lower[1:]
}

// splitSentences splits on whitespace that follows a sentence-ending punctuation mark.
//
// Go compiles regular expressions with RE2, which has no lookbehind — the original
// `(?<=[.!?])\s+` pattern made regexp.MustCompile panic at start-up, so the sidecar's own
// self-test exited 2 before extracting anything. A two-pass scan does the same job.
func splitSentences(text string) []string {
	var out []string
	start := 0
	for i := 0; i < len(text); i++ {
		switch text[i] {
		case '.', '!', '?':
			if i+1 < len(text) && (text[i+1] == ' ' || text[i+1] == '\t' || text[i+1] == '\n') {
				out = append(out, text[start:i+1])
				j := i + 1
				for j < len(text) && (text[j] == ' ' || text[j] == '\t' || text[j] == '\n') {
					j++
				}
				start = j
				i = j - 1
			}
		}
	}
	if start < len(text) {
		out = append(out, text[start:])
	}
	return out
}

func extractClaims(abstract string, limit int) []Claim {
	claims := []Claim{}
	for _, sentence := range splitSentences(abstract) {
		s := strings.TrimSpace(sentence)
		if len(s) < 45 || len(s) > 400 {
			continue
		}
		stance := ""
		for _, sp := range stancePatterns {
			if sp.re.MatchString(s) {
				stance = sp.stance
				break
			}
		}
		if stance == "" {
			continue
		}
		claims = append(claims, Claim{Text: s, Stance: stance})
		if len(claims) >= limit {
			break
		}
	}
	return claims
}

func Extract(p Paper) Extraction {
	// slices start empty, never nil: `json:"topics"` must be [] rather than null, because
	// the consumers (Python ingestion, checks) iterate it directly
	ex := Extraction{
		PaperID:    p.key(),
		Topics:     []string{},
		Methods:    []string{},
		Datasets:   []string{},
		Metrics:    []string{},
		Claims:     []Claim{},
		Provenance: []Provenance{},
	}
	abstract := p.text()
	seen := map[string]bool{}
	add := func(kind string, phrases []struct {
		Phrase string
		Offset int
	}, field string) []string {
		names := []string{}
		for _, h := range phrases {
			name := titleCasePhrase(h.Phrase)
			if !seen[kind+":"+name] {
				seen[kind+":"+name] = true
				ex.Provenance = append(ex.Provenance, Provenance{Entity: name, Kind: kind, Field: field, Offset: h.Offset})
			}
			if !contains(names, name) {
				names = append(names, name)
			}
		}
		return names
	}
	// Title hits are weighted more strongly: they usually name the core topic.
	titleTopics := topicHits(p.Title)
	absTopics := topicHits(abstract)
	if len(titleTopics) == 0 && len(absTopics) == 0 {
		// fall back to the paper's own title keywords so every paper is connected
		absTopics = []struct {
			Phrase string
			Offset int
		}{{Phrase: strings.ToLower(firstMeaningfulWords(p.Title, 3)), Offset: 0}}
	}
	ex.Topics = append(add("Topic", titleTopics, "title"), add("Topic", absTopics, "abstract")...)
	ex.Methods = add("Method", phraseHits(abstract+" "+p.Title, methodDict), "abstract")
	ex.Datasets = add("Dataset", phraseHits(abstract+" "+p.Title, datasetDict), "abstract")
	ex.Metrics = add("Metric", phraseHits(abstract, metricDict), "abstract")
	ex.Claims = extractClaims(abstract, 3)
	ex.Topics = dedupe(ex.Topics)
	ex.Methods = dedupe(ex.Methods)
	ex.Datasets = dedupe(ex.Datasets)
	ex.Metrics = dedupe(ex.Metrics)
	return ex
}

func firstMeaningfulWords(s string, n int) string {
	fields := strings.Fields(regexp.MustCompile(`[^a-zA-Z0-9\- ]`).ReplaceAllString(s, " "))
	var out []string
	for _, f := range fields {
		if len(f) > 3 {
			out = append(out, f)
		}
		if len(out) == n {
			break
		}
	}
	return strings.Join(out, " ")
}

func dedupe(in []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, s := range in {
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	sort.Strings(out)
	return out
}

func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

// ------------------------------------------------------------- embeddings ---

// HashEmbedding is a reproducible lexical embedding: character 3-grams and
// whole words are hashed into a fixed-width, L2-normalised vector. It captures
// lexical overlap only — it is NOT a semantic model. NEXUS labels it as
// `lexical-baseline` everywhere it is used, and swaps to a real embedding
// provider the moment NEXUS_EMBEDDINGS_URL + NEXUS_EMBEDDINGS_KEY are set.
func HashEmbedding(text string, dim int) []float64 {
	vec := make([]float64, dim)
	words := regexp.MustCompile(`[a-z0-9]+`).FindAllString(strings.ToLower(text), -1)
	add := func(tok string, w float64) {
		h := fnv.New64a()
		_, _ = h.Write([]byte(tok))
		idx := int(h.Sum64() % uint64(dim))
		vec[idx] += w
	}
	for _, w := range words {
		add("w:"+w, 1.0)
		if len(w) > 4 {
			for i := 0; i+3 <= len(w); i++ {
				add("g:"+w[i:i+3], 0.35)
			}
		}
	}
	var norm float64
	for _, v := range vec {
		norm += v * v
	}
	if norm > 0 {
		norm = math.Sqrt(norm)
		for i := range vec {
			vec[i] /= norm
		}
	}
	return vec
}

func embeddingViaProvider(ctx context.Context, texts []string, model string) (EmbeddingResponse, error) {
	url := os.Getenv("NEXUS_EMBEDDINGS_URL")
	key := os.Getenv("NEXUS_EMBEDDINGS_KEY")
	if url == "" {
		return EmbeddingResponse{}, fmt.Errorf("NEXUS_EMBEDDINGS_URL not configured")
	}
	body, _ := json.Marshal(EmbeddingRequest{Input: texts, Model: model})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return EmbeddingResponse{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return EmbeddingResponse{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode >= 300 {
		return EmbeddingResponse{}, fmt.Errorf("provider status %d", resp.StatusCode)
	}
	var out EmbeddingResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return EmbeddingResponse{}, err
	}
	out.Engine = "provider-proxy"
	return out, nil
}

func embeddingLocal(texts []string, model string) EmbeddingResponse {
	dim := 384
	if v := os.Getenv("NEXUS_EMBEDDING_DIM"); v != "" {
		fmt.Sscanf(v, "%d", &dim)
	}
	if dim < 64 || dim > 4096 {
		dim = 384
	}
	out := EmbeddingResponse{Object: "list", Model: model, Engine: "lexical-baseline",
		Note: "Deterministic hashed n-gram embedding (lexical baseline, not a semantic model). Set NEXUS_EMBEDDINGS_URL for real semantic vectors."}
	for i, t := range texts {
		sum := sha256.Sum256([]byte(t))
		out.Data = append(out.Data, EmbeddingItem{Object: "embedding", Index: i, Embedding: HashEmbedding(t, dim)})
		_ = binary.BigEndian.Uint32(sum[:4])
	}
	return out
}

// -------------------------------------------------------------- ingestion ---

type FetchResult struct {
	Papers []Paper  `json:"papers"`
	Source string   `json:"source"`
	Errors []string `json:"errors"`
}

// FetchArXiv pulls metadata from the public arXiv Atom API with bounded
// concurrency. Used only when the user explicitly asks for live ingestion; the
// shipped demo corpus always works offline.
func FetchArXiv(ctx context.Context, query string, max int, workers int) FetchResult {
	res := FetchResult{Source: "arxiv"}
	if workers < 1 {
		workers = 4
	}
	if workers > 8 {
		workers = 8
	}
	pageSize := 25
	pages := (max + pageSize - 1) / pageSize
	type page struct{ idx int }
	jobs := make(chan page)
	var mu sync.Mutex
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for p := range jobs {
				url := fmt.Sprintf("http://export.arxiv.org/api/query?search_query=all:%s&start=%d&max_results=%d",
					strings.ReplaceAll(query, " ", "+"), p.idx*pageSize, pageSize)
				req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
				if err != nil {
					mu.Lock()
					res.Errors = append(res.Errors, err.Error())
					mu.Unlock()
					continue
				}
				resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
				if err != nil {
					mu.Lock()
					res.Errors = append(res.Errors, fmt.Sprintf("page %d: %v", p.idx, err))
					mu.Unlock()
					continue
				}
				raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
				resp.Body.Close()
				papers := parseArXivAtom(string(raw))
				mu.Lock()
				res.Papers = append(res.Papers, papers...)
				mu.Unlock()
			}
		}()
	}
	for i := 0; i < pages; i++ {
		jobs <- page{idx: i}
	}
	close(jobs)
	wg.Wait()
	// dedupe by id, keep order stable
	seen := map[string]bool{}
	var out []Paper
	for _, p := range res.Papers {
		if seen[p.ID] {
			continue
		}
		seen[p.ID] = true
		out = append(out, p)
	}
	if len(out) > max {
		out = out[:max]
	}
	res.Papers = out
	return res
}

var entryRe = regexp.MustCompile(`(?s)<entry>(.*?)</entry>`)
var idRe = regexp.MustCompile(`(?s)<id>(.*?)</id>`)
var titleRe = regexp.MustCompile(`(?s)<title>(.*?)</title>`)
var summaryRe = regexp.MustCompile(`(?s)<summary>(.*?)</summary>`)
var pubRe = regexp.MustCompile(`(?s)<published>(\d{4})`)
var nameRe = regexp.MustCompile(`(?s)<name>(.*?)</name>`)

func parseArXivAtom(xml string) []Paper {
	var out []Paper
	for _, m := range entryRe.FindAllStringSubmatch(xml, -1) {
		body := m[1]
		p := Paper{}
		if v := idRe.FindStringSubmatch(body); v != nil {
			p.ID = strings.TrimSpace(v[1])
			p.URL = p.ID
		}
		if v := titleRe.FindStringSubmatch(body); v != nil {
			p.Title = collapse(v[1])
		}
		if v := summaryRe.FindStringSubmatch(body); v != nil {
			p.Abstract = collapse(v[1])
		}
		if v := pubRe.FindStringSubmatch(body); v != nil {
			fmt.Sscanf(v[1], "%d", &p.Year)
		}
		for _, n := range nameRe.FindAllStringSubmatch(body, -1) {
			p.Authors = append(p.Authors, collapse(n[1]))
		}
		if p.Title == "" || p.ID == "" {
			continue
		}
		out = append(out, p)
	}
	return out
}

func collapse(s string) string { return strings.Join(strings.Fields(strings.TrimSpace(s)), " ") }

// ------------------------------------------------------------------ server ---

func serve(port int) {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"ok": true, "service": "nexus-ingest", "version": Version,
			"embedding_engine": engineName(), "go": "1.22+"})
	})
	mux.HandleFunc("/v1/embeddings", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]any{"error": "POST required"})
			return
		}
		var req EmbeddingRequest
		body, err := io.ReadAll(io.LimitReader(r.Body, 4<<20))
		if err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		if err := json.Unmarshal(body, &req); err != nil {
			writeJSON(w, 400, map[string]any{"error": "invalid JSON: " + err.Error()})
			return
		}
		if len(req.Input) == 0 {
			writeJSON(w, 400, map[string]any{"error": "input must contain at least one string"})
			return
		}
		if len(req.Input) > 256 {
			req.Input = req.Input[:256]
		}
		for i, in := range req.Input {
			if len(in) > 20000 {
				req.Input[i] = in[:20000]
			}
		}
		model := req.Model
		if model == "" {
			model = "nexus-lexical-384"
		}
		ctx, cancel := context.WithTimeout(r.Context(), 45*time.Second)
		defer cancel()
		if resp, err := embeddingViaProvider(ctx, req.Input, model); err == nil {
			writeJSON(w, 200, resp)
			return
		}
		writeJSON(w, 200, embeddingLocal(req.Input, model))
	})
	mux.HandleFunc("/extract", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]any{"error": "POST required"})
			return
		}
		var papers []Paper
		body, _ := io.ReadAll(io.LimitReader(r.Body, 16<<20))
		if err := json.Unmarshal(body, &papers); err != nil {
			writeJSON(w, 400, map[string]any{"error": "expected a JSON array of papers"})
			return
		}
		out := make([]Extraction, 0, len(papers))
		for _, p := range papers {
			out = append(out, Extract(p))
		}
		writeJSON(w, 200, map[string]any{"engine": "nexus-ingest-go", "count": len(out), "extractions": out})
	})
	mux.HandleFunc("/fetch/arxiv", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query().Get("q")
		if q == "" {
			writeJSON(w, 400, map[string]any{"error": "q parameter required"})
			return
		}
		maxN := 25
		if v := r.URL.Query().Get("max"); v != "" {
			fmt.Sscanf(v, "%d", &maxN)
		}
		if maxN < 1 || maxN > 200 {
			maxN = 25
		}
		ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
		defer cancel()
		writeJSON(w, 200, FetchArXiv(ctx, q, maxN, 4))
	})

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	log.Printf("nexus-ingest %s listening on %s (embeddings: %s)", Version, addr, engineName())
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		log.Fatalf("server error: %v", err)
	}
}

// loadPapers accepts either a bare array of papers or a NEXUS corpus object, which is what
// `python scripts/build_corpus.py` writes to data/demo/corpus.json.
func loadPapers(raw []byte) ([]Paper, error) {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) > 0 && trimmed[0] == '[' {
		var papers []Paper
		if err := json.Unmarshal(trimmed, &papers); err != nil {
			return nil, fmt.Errorf("corpus is not a JSON array of papers: %w", err)
		}
		return papers, nil
	}
	var wrapper struct {
		Papers []Paper `json:"papers"`
	}
	if err := json.Unmarshal(trimmed, &wrapper); err != nil {
		return nil, fmt.Errorf("corpus is not valid JSON: %w", err)
	}
	if wrapper.Papers == nil {
		return nil, fmt.Errorf("corpus object has no \"papers\" array")
	}
	return wrapper.Papers, nil
}

func engineName() string {
	if os.Getenv("NEXUS_EMBEDDINGS_URL") != "" {
		return "provider-proxy"
	}
	return "lexical-baseline"
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	_ = enc.Encode(v)
}

// -------------------------------------------------------------------- CLI ---

func runTests() int {
	fails := 0
	check := func(name string, cond bool) {
		if cond {
			fmt.Printf("  ok   %s\n", name)
		} else {
			fmt.Printf("  FAIL %s\n", name)
			fails++
		}
	}
	p := Paper{
		ID:    "test-1",
		Title: "Long-Term Memory for Multi-Agent Coordination",
		Abstract: "We study persistent memory in multi-agent systems. " +
			"Our method improves success rate on GAIA by 12% using chain-of-thought planning. " +
			"However, the baseline fails to generalise to unseen environments.",
		Year: 2024,
	}
	ex := Extract(p)
	check("topics extracted", contains(ex.Topics, "Memory Systems") && contains(ex.Topics, "Multi-Agent Systems"))
	check("methods extracted", contains(ex.Methods, "Chain-of-Thought"))
	check("datasets extracted", contains(ex.Datasets, "GAIA"))
	check("metrics extracted", contains(ex.Metrics, "Success Rate"))
	check("claims extracted", len(ex.Claims) >= 2)
	check("provenance recorded", len(ex.Provenance) > 3)
	check("no duplicate topics", len(ex.Topics) == len(dedupe(ex.Topics)))

	v1 := HashEmbedding("multi agent memory", 128)
	v2 := HashEmbedding("multi agent memory", 128)
	v3 := HashEmbedding("quantum chromodynamics lattice", 128)
	check("embedding deterministic", math.Abs(dot(v1, v2)-1.0) < 1e-9)
	check("embedding normalised", math.Abs(dot(v1, v1)-1.0) < 1e-9)
	check("embedding separates unrelated text", dot(v1, v3) < 0.6)

	atom := `<feed><entry><id>http://arxiv.org/abs/2401.00001v1</id><title>A Test Paper</title>
	<summary>We test things.</summary><published>2024-01-01T00:00:00Z</published>
	<author><name>Ada Lovelace</name></author></entry></feed>`
	ps := parseArXivAtom(atom)
	check("arxiv atom parsed", len(ps) == 1 && ps[0].Title == "A Test Paper" && ps[0].Year == 2024)
	check("arxiv author parsed", len(ps) == 1 && ps[0].Authors[0] == "Ada Lovelace")

	if fails == 0 {
		fmt.Println("ALL GO INGESTION TESTS PASSED")
	} else {
		fmt.Printf("%d FAILURES\n", fails)
	}
	return fails
}

func dot(a, b []float64) float64 {
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	var s float64
	for i := 0; i < n; i++ {
		s += a[i] * b[i]
	}
	return s
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: nexus-ingest [serve|extract|fetch|test] [flags]")
		os.Exit(1)
	}
	switch os.Args[1] {
	case "serve":
		port := 8090
		if v := os.Getenv("NEXUS_INGEST_PORT"); v != "" {
			fmt.Sscanf(v, "%d", &port)
		}
		if v := os.Getenv("PORT"); v != "" {
			fmt.Sscanf(v, "%d", &port)
		}
		serve(port)
	case "extract":
		fs := flag.NewFlagSet("extract", flag.ExitOnError)
		corpus := fs.String("corpus", "", "input corpus JSON (array of papers)")
		out := fs.String("out", "", "output path for extractions JSON")
		_ = fs.Parse(os.Args[2:])
		if *corpus == "" {
			log.Fatal("-corpus is required")
		}
		raw, err := os.ReadFile(*corpus)
		if err != nil {
			log.Fatal(err)
		}
		papers, err := loadPapers(raw)
		if err != nil {
			log.Fatalf("%v (expected a JSON array of papers, or a corpus object with a \"papers\" array): %s", err, *corpus)
		}
		extractions := make([]Extraction, 0, len(papers))
		for _, p := range papers {
			extractions = append(extractions, Extract(p))
		}
		payload := map[string]any{"engine": "nexus-ingest-go", "count": len(extractions), "extractions": extractions}
		pretty, _ := json.MarshalIndent(payload, "", "  ")
		if *out == "" {
			os.Stdout.Write(pretty)
		} else {
			if err := os.WriteFile(*out, pretty, 0o644); err != nil {
				log.Fatal(err)
			}
			fmt.Printf("wrote %d extractions to %s\n", len(extractions), *out)
		}
	case "fetch":
		fs := flag.NewFlagSet("fetch", flag.ExitOnError)
		q := fs.String("q", "large language model agents", "arxiv query")
		maxN := fs.Int("max", 50, "max papers")
		out := fs.String("out", "", "output path")
		_ = fs.Parse(os.Args[2:])
		ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
		defer cancel()
		res := FetchArXiv(ctx, *q, *maxN, 4)
		pretty, _ := json.MarshalIndent(res, "", "  ")
		if *out == "" {
			os.Stdout.Write(pretty)
		} else {
			os.WriteFile(*out, pretty, 0o644)
			fmt.Printf("wrote %d papers to %s (%d errors)\n", len(res.Papers), *out, len(res.Errors))
		}
	case "extract-stdin":
		sc := bufio.NewScanner(os.Stdin)
		sc.Buffer(make([]byte, 1<<20), 1<<24)
		var papers []Paper
		if err := json.NewDecoder(os.Stdin).Decode(&papers); err != nil {
			log.Fatal(err)
		}
		for _, p := range papers {
			b, _ := json.Marshal(Extract(p))
			fmt.Println(string(b))
		}
	case "test":
		os.Exit(runTests())
	default:
		fmt.Printf("unknown command %q\n", os.Args[1])
		os.Exit(1)
	}
}
