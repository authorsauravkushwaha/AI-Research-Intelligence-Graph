#!/usr/bin/env ruby
# frozen_string_literal: true

# NEXUS — Research Claim Conflict Resolver (Ruby)
# =============================================================================
# WHY RUBY, AND WHY HERE?
#   Contradiction detection is *natural-language bookkeeping*: negation scope,
#   hedging, comparatives, effect-direction words. Ruby's string handling and
#   compact enumerable blocks make this kind of text-normalisation layer short
#   and readable, which matters when the rules must be auditable by researchers.
#
# WHAT IT DOES
#   * normalises a claim into (subject, predicate, object, direction) tokens
#   * classifies epistemic strength (null / weak / moderate / strong)
#   * detects negation polarity and hedging
#   * groups claims about the same subject+predicate and scores contradiction
#     likelihood for each pair, with a human-readable reason for every score
#
# It is a *linguistic heuristic*, deliberately transparent. NEXUS never presents
# a detected conflict as fact — the API surfaces "potential contradiction" with
# the sentence-level evidence attached.
#
# USAGE
#   echo '{"claims":[{"id":"c1","text":"Scaling model size improves reasoning"},
#                    {"id":"c2","text":"Scaling model size does not improve reasoning"}]}' \
#     | ruby claim_resolver.rb
#
#   ruby claim_resolver.rb --demo      # built-in NEXUS corpus claims
#   ruby claim_resolver.rb --test      # self-test suite (exit 0/1)

require 'json'

module Nexus
  # --- Lexicons -------------------------------------------------------------
  NEGATIONS = %w[
    not no never cannot can't don't doesn't didn't isn't aren't wasn't weren't
    without lacks lack lacking fails fail failed unable nor neither
  ].freeze

  HEDGES_WEAK = %w[
    may might could possibly perhaps potentially suggests suggest indicate
    indicates preliminary tentatively unclear inconclusive some partially
    modest slight marginally occasionally
  ].freeze

  HEDGES_STRONG = %w[
    demonstrates demonstrate proves prove establishes established shows show
    confirms confirm significant significantly consistently robust substantially
    clearly always outperforms surpasses exceeds
  ].freeze

  # Direction words: opposing pairs used to detect directional disagreement
  # (e.g. "increases" vs "decreases").
  # Opposing semantic axes — the same list as the Python reference
  # (backend/ingestion/claims.py). "fail" is deliberately absent: "fail to
  # generalise" is a negation of generalising, not the opposite pole of "improve",
  # so it is handled by the negation detector only.
  DIRECTION_PAIRS = [
    %w[increase decrease], %w[improve degrade], %w[improve reduce], %w[improve harm],
    %w[increase reduce], %w[higher lower], %w[better worse], %w[positive negative],
    %w[outperform underperform], %w[help harm], %w[faster slower], %w[more less],
    %w[enable prevent], %w[support undermine], %w[gain loss], %w[strong weak],
    %w[robust fragile], %w[effective ineffective], %w[benefit harm],
    %w[necessary unnecessary], %w[reduce worsen]
  ].freeze

  STOPWORDS = %w[
    the a an of in on for with to and or is are was were be been being that this
    these those it its as at by from into over under we our their they them
    can could may might not no do does did has have had will would should
  ].freeze

  # A single extracted research claim.
  Claim = Struct.new(:id, :text, :subject, :predicate, :direction, :negated,
                     :strength, :tokens, :source, :paper_id, keyword_init: true)

  # A scored contradiction candidate between two claims.
  Conflict = Struct.new(:claim_a, :claim_b, :score, :kind, :reasons, keyword_init: true)

  class Resolver
    attr_reader :claims

    def initialize
      @claims = []
      @index = {}
    end

    # Parse + register one or more claims; returns the created Claim objects.
    def add(payload)
      # `Array({...})` would explode a single record into its key/value pairs, so a
      # lone Hash is wrapped explicitly: the resolver accepts one claim, a list of
      # claims, or the raw corpus payload.
      records = payload.is_a?(Hash) ? [payload] : Array(payload)
      records.map do |raw|
        c = parse(raw)
        @claims << c
        (@index[group_key(c)] ||= []) << c
        c
      end
    end

    # Split a paper abstract into candidate claim sentences.
    # Only sentences carrying a stance word are kept, so "Background" filler
    # never pollutes the claim graph.
    def claims_from_abstract(text, paper_id: nil, limit: 4)
      return [] if text.nil? || text.strip.empty?

      sentences = text.split(/(?<=[.!?])\s+/).map(&:strip).reject(&:empty?)
      picked = sentences.select { |s| stance?(s) && s.length.between?(40, 400) }
      picked = sentences.select { |s| s.length.between?(40, 400) } if picked.empty?
      picked.first(limit).map.with_index do |s, i|
        parse('id' => "#{paper_id || 'abstract'}::c#{i + 1}", 'text' => s,
              'paper_id' => paper_id, 'source' => 'extracted')
      end
    end

    def parse(raw)
      text = raw['text'].to_s.strip
      toks = tokenize(text)
      Claim.new(
        id: raw['id'] || "claim-#{@claims.size + 1}",
        text: text,
        subject: extract_subject(toks),
        predicate: extract_predicate(toks),
        direction: extract_direction(toks),
        negated: negated?(text),
        strength: strength(toks),
        tokens: toks,
        source: raw['source'] || 'demo-corpus',
        paper_id: raw['paper_id']
      )
    end

    #: Minimum score for a pair to be reported. Matches the Python reference
    #: (backend/ingestion/claims.py) so both engines agree on what a candidate is.
    MIN_SCORE = 0.45

    # All contradiction candidates across the registered claims, ranked.
    #
    # Two passes, exactly as in the Python reference:
    #   1. within groups that share subject+predicate (cheap, precise),
    #   2. across the claims that never grouped (otherwise a slightly different
    #      subject slice would hide a real opposition).
    # Both passes require the *gate*: explicit opposition plus shared substance.
    def conflicts(min_score: MIN_SCORE)
      out = []
      seen = {}

      @index.each_value do |group|
        next if group.size < 2

        group.combination(2) do |a, b|
          next unless gate(a, b)[0]

          seen[[a.id, b.id]] = true
          f = score(a, b)
          out << f if f.score >= min_score
        end
      end

      singles = @claims.select { |c| (@index[group_key(c)] || []).size == 1 }
      singles.combination(2) do |a, b|
        next if seen[[a.id, b.id]]
        next unless gate(a, b)[0]

        f = score(a, b)
        out << f if f.score >= min_score
      end

      out.sort_by { |f| -f.score }
    end

    # A pair is only a candidate when there is explicit opposition *and* enough
    # shared substance to believe they are about the same thing. Mirrors
    # `opposition_gate()` in backend/ingestion/claims.py.
    def gate(a, b)
      return [false, nil, 'same paper — not a contradiction candidate'] if a.paper_id && a.paper_id == b.paper_id

      shared, overlap = shared_content(a, b)
      same_proposition = !a.subject.empty? && a.subject == b.subject && a.predicate == b.predicate

      if a.negated != b.negated && (same_proposition || (shared.size >= 2 && overlap >= 0.2))
        return [true, 'polarity', "polarity opposition with #{shared.size} shared concept token(s)"]
      end
      if a.direction && b.direction && opposing_direction?(a.direction, b.direction) && shared.size >= 2 && overlap >= 0.2
        return [true, 'directional', "opposing direction on the same axis with #{shared.size} shared tokens"]
      end

      [false, nil, 'no explicit opposition signal']
    end

    def shared_content(a, b)
      sa = content_tokens(a.tokens).map { |t| normalise_token(t) }
      sb = content_tokens(b.tokens).map { |t| normalise_token(t) }
      shared = sa & sb
      overlap = if sa.empty? || sb.empty?
                  0.0
                else
                  shared.size.to_f / [sa.size, sb.size].min
                end
      [shared, overlap]
    end

    # Very light stemming, deliberately conservative: only suffixes that are
    # unambiguous for this vocabulary, mirroring the Python `normalise()`.
    def normalise_token(token)
      return "#{token[0..-4]}y" if token.end_with?('ies') && token.length > 4

      %w[ing ed es s].each do |suffix|
        return token[0...-suffix.length] if token.end_with?(suffix) && token.length - suffix.length >= 4
      end
      token
    end

    # Transparent, additive scoring. Every point is explained in `reasons`, which
    # the API returns verbatim so the UI can always justify a flagged conflict.
    def score(a, b)
      reasons = []
      score = 0.0
      kind, _why = gate(a, b).slice(1, 2)

      # The weights below are the published model, identical to the Python reference
      # in backend/ingestion/claims.py: +0.45 polarity, +0.35 opposing direction,
      # +0.20 same subject/predicate, +0.10 both assertive, +0.15 x lexical overlap,
      # -0.20 same polarity and direction (corroboration, not conflict).

      # 1. Explicit negation divergence (the strongest single signal)
      if a.negated != b.negated
        score += 0.45
        reasons << "Polarity differs: one claim is negated (#{a.negated ? a.id : b.id})."
      end

      # 2. Directional disagreement on the same semantic axis
      if a.direction && b.direction && opposing_direction?(a.direction, b.direction)
        score += 0.35
        reasons << "Effect direction is opposite: '#{a.direction}' vs '#{b.direction}'."
      end

      # 3. Same subject and predicate: they really are about the same thing
      if a.subject == b.subject && a.predicate == b.predicate
        score += 0.20
        reasons << "Both claims address the same subject/predicate (#{a.subject} / #{b.predicate})."
      end

      # 4. Same polarity and direction is corroboration, not conflict
      if !a.negated && !b.negated && a.direction && a.direction == b.direction
        score -= 0.20
        reasons << 'Same polarity and same direction — treated as corroboration, not conflict.'
      end

      # 5. Assertive language on both sides makes a disagreement material
      if a.strength == 'strong' && b.strength == 'strong'
        score += 0.10
        reasons << 'Both claims use assertive language, so the disagreement is material.'
      end

      # 6. Lexical overlap of content words (supporting signal only)
      shared, overlap = shared_content(a, b)
      if overlap.positive?
        score += 0.15 * overlap
        reasons << format('%<n>d shared concept tokens (%<p>.0f%% lexical overlap).', n: shared.size, p: overlap * 100)
      end

      Conflict.new(
        claim_a: a, claim_b: b, score: score.clamp(0.0, 1.0).round(3),
        kind: kind || 'contextual', reasons: reasons
      )
    end

    def group_key(c)
      [c.subject, c.predicate].join('|')
    end

    private

    def tokenize(text)
      text.downcase.scan(/[a-z0-9][a-z0-9\-_.]{1,}/).reject { |t| STOPWORDS.include?(t) }
    end

    def content_tokens(toks)
      toks.select { |t| t.length > 3 && !STOPWORDS.include?(t) }
    end

    def extract_subject(toks)
      content_tokens(toks).first(3).join(' ')
    end

    def extract_predicate(toks)
      content_tokens(toks)[3, 3].to_a.join(' ')
    end

    # Negation is a *construction*, not a keyword, and the words that carry it
    # ("not", "no", "never") are stopwords for every other purpose. That is why this
    # check runs on the raw sentence rather than on the filtered token list.
    def negated?(text)
      raw_tokens(text).any? { |t| NEGATIONS.include?(t) || t.include?("n't") }
    end

    def raw_tokens(text)
      text.to_s.downcase.scan(/[a-z0-9][a-z0-9\-_.]{1,}/)
    end

    def strength(toks)
      strong = toks.count { |t| HEDGES_STRONG.include?(t) }
      weak = toks.count { |t| HEDGES_WEAK.include?(t) }
      return 'strong' if strong.positive? && weak.zero?
      return 'weak' if weak.positive? && strong.zero?

      'moderate'
    end

    def stance?(sentence)
      toks = tokenize(sentence)
      strength(toks) != 'moderate' || negated?(toks) || !extract_direction(toks).nil?
    end

    # Mirrors Python's `_direction()`: raw, normalised and de-pluralised forms are
    # all compared, so an inflected verb ("degrades") cannot hide the axis it is on.
    def extract_direction(toks)
      forms = direction_forms(toks)
      DIRECTION_PAIRS.each do |a, b|
        return a if direction_form?(forms, a)
        return b if direction_form?(forms, b)
      end
      nil
    end

    def direction_forms(toks)
      forms = toks.dup
      toks.each do |t|
        forms << normalise_token(t)
        forms << t.sub(/s\z/, '') if t.length > 4 && t.end_with?('s')
      end
      forms.uniq
    end

    def direction_form?(forms, member)
      forms.include?(member) ||
        forms.include?(normalise_token(member)) ||
        (member.length > 4 && member.end_with?('s') && forms.include?(member.sub(/s\z/, '')))
    end

    def opposing_direction?(da, db)
      return false if da.nil? || db.nil?
      return false if da == db

      DIRECTION_PAIRS.any? { |a, b| (da == a && db == b) || (da == b && db == a) } ||
        opposite_in_same_pair?(da, db)
    end

    # "increase/decrease" both belong to the same semantic axis, so any two
    # distinct members of one pair are oppositional.
    def opposite_in_same_pair?(da, db)
      DIRECTION_PAIRS.any? { |pair| pair.include?(da) && pair.include?(db) }
    end

    def lexical_overlap(ta, tb)
      a = content_tokens(ta).uniq
      b = content_tokens(tb).uniq
      return 0.0 if a.empty? || b.empty?

      (a & b).size.to_f / [a.size, b.size].min
    end
  end
end

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# --------------------------------------------------------------- engine API --
module Nexus
  # Run the resolver over a decoded payload ({"claims" => [...]} and/or
  # {"abstracts" => [...]}). The CLI and the HTTP mode both call this, so the two
  # entry points can never produce different answers.
  def self.analyse(payload)
    resolver = Resolver.new
    if payload['abstracts']
      payload['abstracts'].each do |a|
        next unless a['abstract']

        resolver.add(resolver.claims_from_abstract(a['abstract'].to_s, paper_id: a['paper_id']))
      end
    end
    resolver.add(payload['claims'].to_a) if payload['claims']
    {
      'engine' => 'nexus-claim-resolver-ruby',
      'version' => '1.0.0',
      'claims' => resolver.claims.map do |c|
        { 'id' => c.id, 'paper_id' => c.paper_id, 'text' => c.text, 'subject' => c.subject,
          'predicate' => c.predicate, 'direction' => c.direction, 'negated' => c.negated,
          'strength' => c.strength, 'source' => c.source }
      end,
      'conflicts' => resolver.conflicts.map do |f|
        { 'claim_a' => f.claim_a.id, 'claim_b' => f.claim_b.id, 'paper_a' => f.claim_a.paper_id,
          'paper_b' => f.claim_b.paper_id, 'score' => f.score, 'kind' => f.kind, 'reasons' => f.reasons }
      end
    }
  end

  # Minimal HTTP/1.1 service around {Nexus.analyse} — the contract the Python API
  # expects at NEXUS_CLAIM_URL (POST /resolve, GET /health). Stdlib only: no gems,
  # no framework, nothing to install. Sockets do not exist under WASI, so this mode
  # needs a native CRuby (`ruby claim_resolver.rb --serve 8093`).
  def self.serve(port)
    require 'socket'
    server = TCPServer.new('0.0.0.0', port)
    warn "nexus-claim-resolver-ruby listening on 0.0.0.0:#{port}"
    loop do
      socket = server.accept
      Thread.new(socket) do |sock|
        begin
          handle(sock)
        rescue StandardError => e
          warn "request failed: #{e.class}: #{e.message}"
        ensure
          sock.close rescue nil
        end
      end
    end
  end

  def self.handle(socket)
    request = socket.gets
    return unless request

    method, path, = request.split(' ')
    headers = {}
    while (line = socket.gets)
      break if line.strip.empty?

      key, value = line.split(':', 2)
      headers[key.to_s.strip.downcase] = value.to_s.strip
    end
    length = headers['content-length'].to_i
    body = length.positive? ? socket.read(length).to_s : ''

    status, payload = respond(method, path, body)
    socket.write("HTTP/1.1 #{status} #{status == 200 ? 'OK' : 'Bad Request'}\r\n")
    socket.write("Content-Type: application/json; charset=utf-8\r\n")
    socket.write("Access-Control-Allow-Origin: *\r\n")
    socket.write("Access-Control-Allow-Headers: content-type\r\n")
    socket.write("Content-Length: #{payload.bytesize}\r\n")
    socket.write("Connection: close\r\n\r\n")
    socket.write(payload)
  end

  def self.respond(method, path, body)
    route = path.to_s.split('?').first.to_s
    return [200, JSON.generate('ok' => true, 'engine' => 'nexus-claim-resolver-ruby',
                              'version' => '1.0.0', 'ruby' => RUBY_VERSION)] if route == '/health'
    return [200, JSON.generate('ok' => true)] if method == 'OPTIONS'
    return [400, JSON.generate('ok' => false, 'error' => 'POST /resolve expects a JSON body')] unless route == '/resolve'

    payload = JSON.parse(body.to_s)
    [200, JSON.generate(analyse(payload))]
  rescue JSON::ParserError => e
    [400, JSON.generate('ok' => false, 'error' => "invalid JSON: #{e.message}")]
  end
end

if __FILE__ == $PROGRAM_NAME
  resolver = Nexus::Resolver.new

  case ARGV[0]
  when '--test'
    failures = []

    def check(failures, name, cond)
      if cond
        puts "  ok   #{name}"
      else
        puts "  FAIL #{name}"
        failures << name
      end
    end

    r = Nexus::Resolver.new
    r.add('id' => 'a1', 'text' => 'Multi-agent coordination consistently improves reasoning performance.')
    r.add('id' => 'a2', 'text' => 'Multi-agent coordination does not improve reasoning performance.')
    f = r.score(r.claims[0], r.claims[1])
    # 0.57 is what this pair scores in the Python reference too (backend/ingestion/claims.py),
    # verified by the parity fixture in tests/data/claim_parity.json.
    check(failures, 'polarity conflict detected', (f.score - 0.57).abs < 1e-9 && f.kind == 'polarity')
    check(failures, 'strong language classified', r.claims[0].strength == 'strong')
    check(failures, 'reasons are attached', f.reasons.size >= 2)

    r2 = Nexus::Resolver.new
    r2.add('id' => 'b1', 'text' => 'Long-term memory reduces coordination overhead in agent teams.')
    r2.add('id' => 'b2', 'text' => 'Long-term memory increases coordination overhead in agent teams.')
    f2 = r2.score(r2.claims[0], r2.claims[1])
    check(failures, 'directional conflict detected', f2.kind == 'directional')

    r3 = Nexus::Resolver.new
    r3.add('id' => 'c1', 'text' => 'Retrieval augmentation improves factual grounding.')
    r3.add('id' => 'c2', 'text' => 'Retrieval augmentation improves factual grounding and latency.')
    f3 = r3.score(r3.claims[0], r3.claims[1])
    check(failures, 'agreement is NOT flagged as a conflict', f3.score < 0.35)

    r4 = Nexus::Resolver.new
    cs = r4.claims_from_abstract(
      'We propose a hierarchical planner. Notably, our method may improve sample efficiency. ' \
      'Background: agents act in environments. In contrast, prior work fails to generalise.',
      paper_id: 'P-1'
    )
    check(failures, 'abstract split into claims', cs.size >= 2)
    check(failures, 'claim provenance recorded', cs.all? { |c| c.paper_id == 'P-1' })

    puts failures.empty? ? 'ALL RUBY CLAIM-RESOLVER TESTS PASSED' : "#{failures.size} FAILURES"
    exit(failures.empty? ? 0 : 1)

  when '--demo'
    demo = [
      { 'id' => 'NEX-001', 'paper_id' => 'arXiv:2308.08155', 'text' =>
        'Self-reflection with tool feedback consistently improves agent task completion rates.' },
      { 'id' => 'NEX-002', 'paper_id' => 'arXiv:2210.03629', 'text' =>
        'Self-reflection without external feedback does not improve task completion rates on held-out tasks.' },
      { 'id' => 'NEX-003', 'paper_id' => 'arXiv:2305.10601', 'text' =>
        'Increasing the number of sampled reasoning paths improves planning accuracy substantially.' },
      { 'id' => 'NEX-004', 'paper_id' => 'arXiv:2401.03428', 'text' =>
        'Increasing the number of sampled reasoning paths reduces planning accuracy because of error compounding.' },
      { 'id' => 'NEX-005', 'paper_id' => 'arXiv:2308.00352', 'text' =>
        'Long-term memory reduces redundant tool calls in multi-agent workflows.' }
    ]
    resolver.add(demo)
    puts JSON.pretty_generate(
      'analyzed_claims' => resolver.claims.size,
      'conflicts' => resolver.conflicts.map do |f|
        {
          'claim_a' => f.claim_a.id, 'claim_b' => f.claim_b.id,
          'paper_a' => f.claim_a.paper_id, 'paper_b' => f.claim_b.paper_id,
          'score' => f.score, 'kind' => f.kind, 'reasons' => f.reasons
        }
      end
    )

  when '--serve'
    Nexus.serve((ARGV[1] || ENV['NEXUS_CLAIM_PORT'] || '8093').to_i)

  else
    raw = ARGV[0] ? File.read(ARGV[0]) : $stdin.read
    puts JSON.generate(Nexus.analyse(JSON.parse(raw)))
  end
end

