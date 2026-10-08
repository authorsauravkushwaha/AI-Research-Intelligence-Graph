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
  DIRECTION_PAIRS = [
    %w[increase decreases],
    %w[improve degrade],
    %w[improves degrades],
    %w[reduce increases],
    %w[reduces increases],
    %w[higher lower],
    %w[better worse],
    %w[positive negative],
    %w[outperforms underperforms],
    %w[scales fails],
    %w[faster slower],
    %w[more less],
    %w[enable prevent],
    %w[enables prevents],
    %w[supports undermines],
    %w[helps harms],
    %w[gain loss],
    %w[strong weak]
  ].map { |a, b| [a, b] }.freeze

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
      Array(payload).map do |raw|
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
        negated: negated?(toks),
        strength: strength(toks),
        tokens: toks,
        source: raw['source'] || 'demo-corpus',
        paper_id: raw['paper_id']
      )
    end

    # All contradiction candidates across the registered claims, ranked.
    def conflicts(min_score: 0.35)
      out = []
      @index.each_value do |group|
        next if group.size < 2

        group.combination(2) do |a, b|
          f = score(a, b)
          out << f if f.score >= min_score
        end
      end
      out.sort_by { |f| -f.score }
    end

    # Transparent, additive scoring. Every point is explained in `reasons`, which
    # the API returns verbatim so the UI can always justify a flagged conflict.
    def score(a, b)
      reasons = []
      score = 0.0

      # 1. Overlapping subject+predicate -> they really are about the same thing
      if a.subject == b.subject && a.predicate == b.predicate
        score += 0.40
        reasons << "Both claims address the same subject/predicate (#{a.subject} / #{a.predicate})."
      end

      # 2. Explicit negation divergence
      if a.negated != b.negated
        score += 0.30
        reasons << "Polarity differs: one claim is negated (#{a.negated ? a.id : b.id})."
      end

      # 3. Directional disagreement
      if a.direction && b.direction && opposing_direction?(a.direction, b.direction)
        score += 0.25
        reasons << "Effect direction is opposite: '#{a.direction}' vs '#{b.direction}'."
      end

      # 4. Same-strength mutual affirmation is *agreement*, not conflict
      if !a.negated && !b.negated && a.direction && a.direction == b.direction
        score -= 0.20
        reasons << 'Same polarity and same direction — treated as corroboration, not conflict.'
      end

      # 5. Confident language on both sides makes a conflict more interesting
      if a.strength == 'strong' && b.strength == 'strong'
        score += 0.10
        reasons << 'Both claims use assertive language, so the disagreement is material.'
      end

      # 6. Lexical overlap of content words (weak corroboration of "same topic")
      overlap = lexical_overlap(a.tokens, b.tokens)
      score += 0.15 * overlap if overlap.positive?
      reasons << format('Token overlap %.0f%% between the two claims.', overlap * 100) if overlap >= 0.3

      kind =
        if a.negated != b.negated then 'polarity'
        elsif opposing_direction?(a.direction, b.direction) then 'directional'
        else 'contextual'
        end

      Conflict.new(claim_a: a, claim_b: b, score: score.clamp(0.0, 1.0).round(3), kind: kind, reasons: reasons)
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

    def negated?(toks)
      toks.any? { |t| NEGATIONS.include?(t) || t.include?("n't") }
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

    def extract_direction(toks)
      DIRECTION_PAIRS.each do |a, b|
        return a if toks.include?(a)
        return b if toks.include?(b)
      end
      nil
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
    check(failures, 'polarity conflict detected', f.score >= 0.6 && f.kind == 'polarity')
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

  else
    raw = ARGV[0] ? File.read(ARGV[0]) : $stdin.read
    payload = JSON.parse(raw)
    if payload['abstracts']
      payload['abstracts'].each do |a|
        resolver.add(resolver.claims_from_abstract(a['abstract'].to_s, paper_id: a['paper_id'])) if a['abstract']
      end
    end
    resolver.add(payload['claims'].to_a) if payload['claims']
    puts JSON.generate(
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
    )
  end
end
