"""Curated demo corpus data for NEXUS.

Editorial policy, enforced by scripts/build_corpus.py:

  * ``arxiv_id`` and ``title`` are real public arXiv records, collected from arXiv
    listings and the community index archived in data/demo/sources/.
  * ``summary`` is an editorial scope note written for this demo, never a quotation.
    Records without a note carry ``summary_source='taxonomy'`` and only their tags.
  * ``authors`` are present only when authorship was verified; otherwise the record
    says ``author_status='not-collected'`` and no names are invented.
  * ``LINEAGE`` is a curated, high-confidence subset of real citation lineage —
    a demonstration graph, not a citation graph.
  * ``CLAIMS`` are editorial distillations of what each paper reports. The seven
    tension pairs at the top of the list state a finding together with the opposing
    finding (with an explicit negation), sharing enough vocabulary for the resolver's
    conservative opposition gate. A detected tension is a candidate to check — never
    a claim that the authors disagree.

  Verify every record at its arXiv URL (https://arxiv.org/abs/<id>) before citing it.

Generate ``data/demo/corpus.json`` with::

    python scripts/build_corpus.py
"""

#: (arxiv_id, title, field, topics, methods, datasets, authors)
CLASSICS: list[tuple] = [
    ('2402.05120', 'More Agents Is All You Need', 'Multi-Agent Systems', ['Multi-Agent Systems', 'Sampling', 'Scaling'], ['Agent Sampling'], ['Reasoning benchmarks'], ['Junyou Li', 'Qin Zhang', 'Yangbin Yu', 'Qiang Fu', 'Deheng Ye']),
    ('2404.16130', 'From Local to Global: A Graph RAG Approach to Query-Focused Summarization', 'Retrieval and RAG', ['Graph RAG', 'Summarization', 'Knowledge Graphs'], ['GraphRAG'], ['Podcast transcripts', 'News datasets'], ['Darren Edge', 'Ha Trinh', 'Newman Cheng', 'Joshua Bradley', 'Alex Chao', 'Apurva Mody', 'Steven Truitt', 'Dasha Metropolitansky']),
    ('2302.04761', 'Toolformer: Language Models Can Teach Themselves to Use Tools', 'Tool Use and APIs', ['Tool Use', 'Self-Supervised Learning'], ['Toolformer'], ['CCNet'], ['Timo Schick', 'Jane Dwivedi-Yu', 'Roberto Dessi', 'Roberta Raileanu', 'Maria Lomeli', 'Eric Hambro', 'Luke Zettlemoyer', 'Nicola Cancedda']),
    ('2303.11366', 'Reflexion: Language Agents with Verbal Reinforcement Learning', 'Agent Architectures', ['Self-Reflection', 'Agent Architectures', 'Memory'], ['Reflexion', 'Verbal Reinforcement Learning'], ['HumanEval', 'ALFWorld'], ['Noah Shinn', 'Federico Cassano', 'Beck Labash', 'Ashwin Gopinath', 'Karthik Narasimhan', 'Shunyu Yao']),
    ('2304.03442', 'Generative Agents: Interactive Simulacra of Human Behavior', 'Simulation and Society', ['Simulation', 'Memory', 'Agent Societies'], ['Generative Agents'], ['Smallville simulation'], ['Joon Sung Park', "Joseph C. O'Brien", 'Carrie J. Cai', 'Meredith Ringel Morris', 'Percy Liang', 'Michael S. Bernstein']),
    ('2305.10601', 'Tree of Thoughts: Deliberate Problem Solving with Large Language Models', 'Reasoning and Inference', ['Chain-of-Thought', 'Search', 'Reasoning'], ['Tree of Thoughts'], ['Game of 24', 'Creative Writing'], ['Shunyu Yao', 'Dian Yu', 'Jeffrey Zhao', 'Izhak Shafran', 'Thomas L. Griffiths', 'Yuan Cao', 'Karthik Narasimhan']),
    ('2305.15334', 'Gorilla: Large Language Model Connected with Massive APIs', 'Tool Use and APIs', ['Tool Use', 'API Calling'], ['Gorilla'], ['APIBench'], ['Shishir G. Patil', 'Tianjun Zhang', 'Xin Wang', 'Joseph E. Gonzalez']),
    ('2306.05685', 'Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena', 'Benchmarks and Evaluation', ['Evaluation', 'LLM-as-a-Judge'], ['LLM-as-a-Judge'], ['MT-Bench', 'Chatbot Arena'], ['Lianmin Zheng', 'Wei-Lin Chiang', 'Ying Sheng', 'Siyuan Zhuang', 'Zhanghao Wu', 'Yonghao Zhuang', 'Zi Lin', 'Zhuohan Li']),
    ('2307.16789', 'ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs', 'Tool Use and APIs', ['Tool Use', 'API Calling', 'Instruction Tuning'], ['ToolLLM', 'DFSDT'], ['ToolBench'], ['Yujia Qin', 'Shihao Liang', 'Yining Ye', 'Kunlun Zhu', 'Lan Yan', 'Yaxi Lu', 'Yankai Lin', 'Xin Cong']),
    ('2308.00352', 'MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework', 'Multi-Agent Systems', ['Multi-Agent Systems', 'Software Agents', 'Coordination'], ['MetaGPT', 'SOP Prompting'], ['HumanEval', 'MBPP', 'SoftwareDev'], ['Sirui Hong', 'Mingchen Zhuge', 'Jonathan Chen', 'Xiawu Zheng', 'Yuheng Cheng', 'Ceyao Zhang', 'Jinlin Wang', 'Zili Wang']),
    ('2308.03688', 'AgentBench: Evaluating LLMs as Agents', 'Benchmarks and Evaluation', ['Evaluation', 'Agent Benchmarks'], ['AgentBench'], ['AgentBench'], ['Xiao Liu', 'Hao Yu', 'Hanchen Zhang', 'Yifan Xu', 'Xuanyu Lei', 'Hanyu Lai', 'Yu Gu', 'Yuxian Gu']),
    ('2308.08155', 'AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation', 'Multi-Agent Systems', ['Multi-Agent Systems', 'Conversational Agents', 'Coordination'], ['AutoGen'], ['Agent benchmarks'], ['Qingyun Wu', 'Gagan Bansal', 'Jieyu Zhang', 'Yiran Wu', 'Beibin Li', 'Erkang Zhu', 'Li Jiang', 'Xiaoyun Zhang']),
    ('2310.08560', 'MemGPT: Towards LLMs as Operating Systems', 'Agent Memory', ['Agent Memory', 'Context Management', 'Operating Systems'], ['MemGPT', 'Virtual Context Management'], ['Document QA', 'Multi-session chat'], ['Charles Packer', 'Sarah Wooders', 'Kevin Lin', 'Vivian Fang', 'Shishir G. Patil', 'Ion Stoica', 'Joseph E. Gonzalez']),
    ('2310.11511', 'Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection', 'Retrieval and RAG', ['Retrieval-Augmented Generation', 'Self-Reflection'], ['Self-RAG', 'Reflection Tokens'], ['PopQA', 'TriviaQA', 'PubHealth'], ['Akari Asai', 'Zeqiu Wu', 'Yizhong Wang', 'Avirup Sil', 'Hannaneh Hajishirzi']),
    ('2311.12983', 'GAIA: a benchmark for General AI Assistants', 'Benchmarks and Evaluation', ['Evaluation', 'Agent Benchmarks', 'Tool Use'], ['GAIA'], ['GAIA'], ['Grégoire Mialon', 'Clémentine Fourrier', 'Craig Swift', 'Thomas Wolf', 'Yann LeCun', 'Thomas Scialom']),
    ('2201.11903', 'Chain-of-Thought Prompting Elicits Reasoning in Large Language Models', 'Reasoning and Inference', ['Chain-of-Thought', 'Prompting', 'Reasoning'], ['Chain-of-Thought'], ['GSM8K', 'SVAMP', 'StrategyQA'], ['Jason Wei', 'Xuezhi Wang', 'Dale Schuurmans', 'Maarten Bosma', 'Brian Ichter', 'Fei Xia', 'Ed H. Chi', 'Quoc V. Le']),
    ('2203.02155', 'Training language models to follow instructions with human feedback', 'Alignment and Safety', ['Alignment', 'Reinforcement Learning from Human Feedback'], ['RLHF', 'InstructGPT'], ['API prompts'], ['Long Ouyang', 'Jeffrey Wu', 'Xu Jiang', 'Diogo Almeida', 'Carroll L. Wainwright', 'Pamela Mishkin', 'Chong Zhang', 'Sandhini Agarwal']),
    ('2203.11171', 'Self-Consistency Improves Chain of Thought Reasoning in Language Models', 'Reasoning and Inference', ['Chain-of-Thought', 'Reasoning', 'Decoding'], ['Self-Consistency'], ['GSM8K', 'SVAMP', 'AQuA'], ['Xuezhi Wang', 'Jason Wei', 'Dale Schuurmans', 'Quoc V. Le', 'Ed H. Chi', 'Sharan Narang', 'Aakanksha Chowdhery', 'Denny Zhou']),
    ('2205.11916', 'Large Language Models are Zero-Shot Reasoners', 'Reasoning and Inference', ['Chain-of-Thought', 'Prompting', 'Reasoning'], ['Zero-Shot Chain-of-Thought'], ['MultiArith', 'GSM8K'], ['Takeshi Kojima', 'Shixiang Shane Gu', 'Machel Reid', 'Yutaka Matsuo', 'Yusuke Iwasawa']),
    ('2210.03629', 'ReAct: Synergizing Reasoning and Acting in Language Models', 'Agent Architectures', ['Tool Use', 'Reasoning', 'Agent Architectures'], ['ReAct', 'Tool Use'], ['HotpotQA', 'FEVER', 'ALFWorld'], ['Shunyu Yao', 'Jeffrey Zhao', 'Dian Yu', 'Nan Du', 'Izhak Shafran', 'Karthik Narasimhan', 'Yuan Cao']),
    ('2212.06817', 'RT-1: Robotics Transformer for Real-World Control at Scale', 'Embodied and Vision', ['Robotics', 'Vision-Language Models', 'Imitation Learning'], ['Robotics Transformer'], ['Robotics tasks'], ['Anthony Brohan', 'Noah Brown', 'Justice Carbajal', 'Yevgen Chebotar', 'Joseph Dabis', 'Chelsea Finn', 'Keerthana Gopalakrishnan', 'Karol Hausman']),
    ('2103.01955', 'The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games', 'Multi-Agent RL', ['Multi-Agent Systems', 'Policy Optimization', 'Reinforcement Learning'], ['MAPPO', 'PPO'], ['SMAC', 'Hanabi', 'MPE'], ['Chao Yu', 'Akash Velu', 'Eugene Vinitsky', 'Jiajun Gao', 'Yu Wang', 'Alexandre Bayen', 'Yi Wu']),
    ('2106.09685', 'LoRA: Low-Rank Adaptation of Large Language Models', 'Model Architectures', ['Parameter-Efficient Fine-Tuning', 'Large Language Models'], ['LoRA'], ['GLUE', 'E2E NLG'], ['Edward J. Hu', 'Yelong Shen', 'Phillip Wallis', 'Zeyuan Allen-Zhu', 'Yuanzhi Li', 'Shean Wang', 'Lu Wang', 'Weizhu Chen']),
    ('2005.11401', 'Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks', 'Retrieval and RAG', ['Retrieval-Augmented Generation', 'Knowledge-Intensive NLP'], ['RAG', 'Dense Retrieval'], ['Natural Questions', 'TriviaQA', 'FEVER'], ['Patrick Lewis', 'Ethan Perez', 'Aleksandra Piktus', 'Fabio Petroni', 'Vladimir Karpukhin', 'Naman Goyal', 'Heinrich Kuttler', 'Mike Lewis']),
    ('2005.14165', 'Language Models are Few-Shot Learners', 'Model Architectures', ['Large Language Models', 'In-Context Learning'], ['GPT-3'], ['SuperGLUE', 'TriviaQA'], ['Tom B. Brown', 'Benjamin Mann', 'Nick Ryder', 'Melanie Subbiah', 'Jared Kaplan', 'Prafulla Dhariwal', 'Amanda Askell', 'Sandhini Agarwal']),
    ('1806.01261', 'Relational inductive biases, deep learning, and graph networks', 'Graph Learning', ['Graph Neural Networks', 'Relational Reasoning'], ['Graph Networks'], ['CLEVR'], ['Peter W. Battaglia', 'Jessica B. Hamrick', 'Victor Bapst', 'Alvaro Sanchez-Gonzalez', 'Vinicius Zambaldi', 'Mateusz Malinowski', 'Andrea Tacchetti', 'David Raposo']),
    ('1810.04805', 'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding', 'Model Architectures', ['Pre-training', 'Transformers', 'Language Understanding'], ['BERT', 'Masked Language Modeling'], ['GLUE', 'SQuAD'], ['Jacob Devlin', 'Ming-Wei Chang', 'Kenton Lee', 'Kristina Toutanova']),
    ('1706.02275', 'Multi-Agent Actor-Critic for Mixed Cooperative-Competitive Environments', 'Multi-Agent RL', ['Multi-Agent Systems', 'Actor-Critic', 'Reinforcement Learning'], ['MADDPG'], ['Multi-Agent Particle Environments'], ['Ryan Lowe', 'Yi Wu', 'Aviv Tamar', 'Jean Harb', 'Pieter Abbeel', 'Igor Mordatch']),
    ('1706.03762', 'Attention Is All You Need', 'Model Architectures', ['Transformers', 'Attention Mechanisms'], ['Transformer', 'Self-Attention'], ['WMT 2014'], ['Ashish Vaswani', 'Noam Shazeer', 'Niki Parmar', 'Jakob Uszkoreit', 'Llion Jones', 'Aidan N. Gomez', 'Lukasz Kaiser', 'Illia Polosukhin']),
    ('1707.06347', 'Proximal Policy Optimization Algorithms', 'Reinforcement Learning', ['Policy Optimization', 'Reinforcement Learning'], ['PPO'], ['Atari 2600', 'MuJoCo'], ['John Schulman', 'Filip Wolski', 'Prafulla Dhariwal', 'Alec Radford', 'Oleg Klimov']),
    ('1710.10903', 'Graph Attention Networks', 'Graph Learning', ['Graph Neural Networks', 'Attention Mechanisms'], ['GAT'], ['Cora', 'Citeseer', 'Pubmed'], ['Petar Velickovic', 'Guillem Cucurull', 'Arantxa Casanova', 'Adriana Romero', 'Pietro Lio', 'Yoshua Bengio']),
    ('1609.02907', 'Semi-Supervised Classification with Graph Convolutional Networks', 'Graph Learning', ['Graph Neural Networks', 'Semi-Supervised Learning'], ['GCN'], ['Cora', 'Citeseer', 'Pubmed'], ['Thomas N. Kipf', 'Max Welling']),
    ('1502.05477', 'Trust Region Policy Optimization', 'Reinforcement Learning', ['Policy Optimization', 'Reinforcement Learning'], ['TRPO'], ['MuJoCo', 'Atari 2600'], ['John Schulman', 'Sergey Levine', 'Philipp Moritz', 'Michael I. Jordan', 'Pieter Abbeel']),
    ('1312.5602', 'Playing Atari with Deep Reinforcement Learning', 'Reinforcement Learning', ['Deep Reinforcement Learning', 'Value-Based RL'], ['DQN'], ['Atari 2600'], ['Volodymyr Mnih', 'Koray Kavukcuoglu', 'David Silver', 'Alex Graves', 'Ioannis Antonoglou', 'Daan Wierstra', 'Martin Riedmiller']),
]

#: (arxiv_id, title, field, topics, methods, datasets, authors)
VERIFIED: list[tuple] = [
    ('2501.13956', 'Zep: A Temporal Knowledge Graph Architecture for Agent Memory', 'Agent Memory', ['Agent Memory', 'Knowledge Graphs', 'Temporal Reasoning'], ['Knowledge Graphs', 'Agentic Memory', 'Retrieval-Augmented Generation'], ['Long-term conversation benchmarks'], []),
    ('2505.16067', 'How Memory Management Impacts LLM Agents: An Empirical Study of Experience-Following Behavior', 'Agent Memory', ['Agent Memory', 'Memory Management', 'Evaluation'], ['Empirical Study', 'Agentic Memory'], ['Long-horizon agent tasks'], ['Zidi Xiong', 'Yuping Lin', 'Wenya Xie', 'Pengfei He', 'Zirui Liu', 'Jiliang Tang', 'Himabindu Lakkaraju', 'Zhen Xiang']),
    ('2506.07398', 'G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems', 'Agent Memory', ['Agent Memory', 'Multi-Agent Systems', 'Hierarchical Memory'], ['Agentic Memory', 'Multi-Agent Collaboration'], ['ALFWorld', 'PDDL'], ['Guibin Zhang', 'Muxin Fu', 'Guancheng Wan', 'Miao Yu', 'Kun Wang', 'Shuicheng Yan']),
    ('2507.07957', 'MIRIX: Multi-Agent Memory System for LLM-Based Agents', 'Agent Memory', ['Agent Memory', 'Multi-Agent Systems', 'Memory Architecture'], ['Agentic Memory', 'Multi-Agent Collaboration'], ['Screenshots', 'Long conversation benchmarks'], []),
    ('2508.08997', 'Intrinsic Memory Agents: Heterogeneous Multi-Agent LLM Systems through Structured Contextual Memory', 'Agent Memory', ['Agent Memory', 'Multi-Agent Systems', 'Context Management'], ['Agentic Memory', 'Multi-Agent Collaboration'], ['PDDL', 'ALFWorld', 'FEVER'], []),
    ('2510.04851', 'Legomem: Modular Procedural Memory for Multi-Agent LLM Systems for Workflow Automation', 'Agent Memory', ['Agent Memory', 'Multi-Agent Systems', 'Workflow Automation'], ['Agentic Memory', 'Multi-Agent Collaboration', 'Workflow Orchestration'], ['Workflow automation tasks'], ['Dongge Han', 'Camille Couturier', 'Daniel Madrigal Diaz', 'Xuchao Zhang', 'Victor Ruehle', 'Saravan Rajmohan']),
]

#: (arxiv_id, title, field, topics, methods, datasets)
POOL: list[tuple] = [
    ('2004.07213', 'Toward Trustworthy AI Development: Mechanisms for Supporting Verifiable Claims', 'Safety and Security', ['Verification', 'Safety', 'Reinforcement Learning'], ['Verification', 'Reinforcement Learning'], []),
    ('2108.07258', 'On the Opportunities and Risks of Foundation Models', 'Safety and Security', ['Reinforcement Learning'], ['Reinforcement Learning'], []),
    ('2112.04359', 'Ethical and social risks of harm from Language Models', 'Simulation and Society', ['Agent Societies'], ['Large Language Models'], []),
    ('2112.09332', 'WebGPT: Browser-assisted question-answering with human feedback', 'Agent Systems', ['Alignment', 'Web Agents', 'Human-AI Interaction'], [], []),
    ('2303.17760', 'CAMEL: Communicative Agents for "Mind" Exploration of Large Language Model Society', 'Simulation and Society', ['Agent Societies'], ['Large Language Models'], []),
    ('2304.05376', 'ChemCrow: Augmenting large-language models with chemistry tools', 'Tool Use and APIs', ['Tool Use', 'Scientific Discovery'], ['Tool Learning', 'Large Language Models'], []),
    ('2304.07590', 'Self-collaboration Code Generation via ChatGPT', 'Software Agents', ['Software Agents', 'Coordination', 'Scientific Discovery'], ['Multi-Agent Collaboration'], []),
    ('2305.04032', 'ToolCoder: Teach Code Generation Models to use API search tools', 'Software Agents', ['API Calling', 'Software Agents', 'Tool Use'], ['Search', 'Tool Learning'], []),
    ('2305.14325', 'Improving Factuality and Reasoning in Language Models through Multiagent Debate', 'Multi-Agent Systems', ['Debate', 'Reasoning', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2305.16504', 'On the Tool Manipulation Capability of Open-source Large Language Models', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2305.17126', 'LARGE LANGUAGE MODELS AS TOOL MAKERS', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2305.19118', 'Encouraging Divergent Thinking in Large Language Models through Multi-Agent Debate', 'Multi-Agent Systems', ['Debate', 'Reasoning', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2306.03314', 'Multi-Agent Collaboration: Harnessing the Power of Intelligent LLM Agents', 'Multi-Agent Systems', ['Coordination', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2306.05301', 'ToolAlpaca: Generalized Tool Learning for Language Models with 3000 Simulated Cases', 'Simulation and Society', ['Agent Societies', 'Simulation', 'Tool Use'], ['Simulation', 'Tool Learning', 'Large Language Models'], []),
    ('2306.06624', 'RestGPT: Connecting Large Language Models with Real-World RESTful APIs', 'Simulation and Society', ['API Calling'], ['Large Language Models'], []),
    ('2306.16207', 'Inferring the Goals of Communicating Agents from Actions and Instructions', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2307.04738', 'Roco: Dialectic multi-robot collaboration with large language models', 'Embodied and Vision', ['Embodied Agents', 'Coordination'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2307.07924', 'ChatDev: Communicative Agents for Software Development', 'Software Agents', ['Software Agents'], [], []),
    ('2308.03427', 'TPTU: Large Language Model-based AI Agents for Task Planning and Tool Usage', 'Tool Use and APIs', ['Planning', 'Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2308.11432', 'A survey on large language model based autonomous agents', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2308.16505', 'Recommender AI Agent: Integrating Large Language Models for Interactive Recommendations', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2309.02427', 'Cognitive Architectures for Language Agents', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2309.03736', 'TradingGPT: Multi-Agent System with Layered Memory and Distinct Characters for Enhanced Financial Trading Performance', 'Agent Memory', ['Role Assignment', 'Agent Memory', 'Multi-Agent Systems'], ['Agentic Memory', 'Multi-Agent Collaboration'], []),
    ('2309.04658', 'Exploring large language models for communication games: An empirical study on Werewolf', 'Simulation and Society', ['Simulation'], ['Large Language Models'], []),
    ('2309.07864', 'The rise and potential of large language model based agents: a survey', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2309.13007', 'ReConcile: Round-Table Conference Improves Reasoning via Consensus among Diverse LLMs', 'Agent Architectures', ['Reasoning'], [], []),
    ('2309.15025', 'Large Language Model Alignment: A Survey', 'Agent Systems', ['Alignment'], ['Large Language Models'], []),
    ('2309.17288', 'AutoAgents: A Framework for Automatic Agent Generation', 'Scientific and Medical Agents', ['Scientific Discovery'], [], []),
    ('2310.02170', 'A Dynamic LLM-Powered Agent Network for Task-Oriented Agent Collaboration', 'Multi-Agent Systems', ['Distributed Systems', 'Coordination'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2310.05915', 'FireAct: Toward Language Agent Fine-tuning', 'Agent Architectures', ['Finetuning'], ['Fine-Tuning'], []),
    ('2310.10108', 'On Generative Agents in Recommendation', 'Scientific and Medical Agents', ['Scientific Discovery'], [], []),
    ('2310.17512', 'CompeteAI: Understanding the Competition Dynamics in Large Language Model-based Agents', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2311.11315', 'TPTU-v2: Boosting Task Planning and Tool Usage of Large Language Model-based Agents in Real-world Systems', 'Simulation and Society', ['Planning', 'Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2311.17227', 'War and Peace (WarAgent): Large Language Model-based Multi-Agent Simulation of World Wars', 'Simulation and Society', ['Agent Societies', 'Simulation', 'Multi-Agent Systems'], ['Simulation', 'Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2312.04372', 'LaMPilot: An Open Benchmark Dataset for Autonomous Driving with Language Model Programs', 'Software Agents', ['Embodied Agents', 'Software Agents', 'Evaluation'], ['Benchmarking', 'Large Language Models'], ['LaMPilot']),
    ('2312.13010', 'AgentCoder: Multi-Agent-based Code Generation with Iterative Testing and Optimisation', 'Software Agents', ['Software Agents', 'Scientific Discovery', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2401.03428', 'Exploring Large Language Model based Intelligent Agents: Definitions, Methods, and Prospects', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2401.03568', 'Agent AI: Surveying the Horizons of Multimodal Interaction', 'Embodied and Vision', ['Vision-Language Models'], [], []),
    ('2401.05268', 'AutoAct: Automatic Agent Learning from Scratch for QA via Self-Planning', 'Agent Architectures', ['Planning'], [], []),
    ('2401.05459', 'Personal LLM Agents: Insights and Survey about the Capability, Efficiency and Security', 'Safety and Security', ['Role Assignment', 'Security'], ['Large Language Models'], []),
    ('2401.06201', 'EASYTOOL: Enhancing LLM-based Agents with Concise Tool Instruction', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2401.10019', 'R-Judge: Benchmarking Safety Risk Awareness for LLM Agents', 'Benchmarks and Evaluation', ['Safety', 'Evaluation'], ['Benchmarking', 'Large Language Models'], []),
    ('2401.11880', 'PsySafe: A Comprehensive Framework for Psychological-based Attack, Defense, and Evaluation of Multi-agent System Safety', 'Benchmarks and Evaluation', ['Safety', 'Security', 'Evaluation'], ['Benchmarking', 'Multi-Agent Collaboration'], []),
    ('2401.12954', 'Meta-Prompting: Enhancing Language Models with Task-Agnostic Scaffolding', 'Agent Systems', ['Agent Systems'], ['Prompt Engineering', 'Large Language Models'], []),
    ('2401.17167', 'Planning, Creation, Usage: Benchmarking LLMs for Comprehensive Tool Utilization in Real-World Complex Scenarios', 'Benchmarks and Evaluation', ['Planning', 'Evaluation', 'Tool Use'], ['Benchmarking', 'Tool Learning'], []),
    ('2402.00262', 'Computational Experiments Meet Large Language Model Based Agents: A Survey and Perspective', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2402.01030', 'Executable Code Actions Elicit Better LLM Agents', 'Software Agents', ['Software Agents'], ['Large Language Models'], []),
    ('2402.01586', 'TrustAgent: Towards Safe and Trustworthy LLM-based Agents', 'Safety and Security', ['Safety'], ['Large Language Models'], []),
    ('2402.01680', 'Large Language Model based Multi-Agents: A Survey of Progress and Challenges', 'Multi-Agent Systems', ['Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2402.02716', 'Understanding the planning of LLM agents: A survey', 'Agent Architectures', ['Planning'], ['Large Language Models'], []),
    ('2402.06782', 'Debating with More Persuasive LLMs Leads to More Truthful Answers', 'Agent Systems', ['Agent Systems'], [], []),
    ('2402.08567', 'Agent Smith: A Single Image Can Jailbreak One Million Multimodal LLM Agents Exponentially Fast', 'Embodied and Vision', ['Vision-Language Models', 'Security'], ['Large Language Models'], []),
    ('2402.10196', 'A Trembling House of Cards? Mapping Adversarial Attacks against Language Agents', 'Safety and Security', ['Security'], [], []),
    ('2402.15116', 'Large Multimodal Agents: A Survey', 'Embodied and Vision', ['Vision-Language Models'], [], []),
    ('2402.15506', 'AgentOhana: Design Unified Data and Training Pipeline for Effective Agent Learning', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2403.00833', 'Position Paper: Agent AI Towards a Holistic Intelligence', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2403.00839', 'ToolNet: Connecting Large Language Models with Massive Tools via Tool Graph', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2403.03636', 'SheetAgent: Towards A Generalist Agent for Spreadsheet Reasoning and Manipulation via Large Language Models', 'Software Agents', ['Data Science Agents', 'Reasoning', 'Scientific Discovery'], ['Large Language Models'], []),
    ('2403.19962', 'Enhancing the General Agent Capabilities of Low-Parameter LLMs through Tuning and Multi-Branch Reasoning', 'Scientific and Medical Agents', ['Finetuning', 'Reasoning', 'Scientific Discovery'], ['Fine-Tuning'], []),
    ('2404.11584', 'The Landscape of Emerging AI Agent Architectures for Reasoning, Planning, and Tool Calling: A Survey', 'Tool Use and APIs', ['Planning', 'Reasoning', 'Tool Use'], ['Tool Learning'], []),
    ('2404.13501', 'A Survey on the Memory Mechanism of Large Language Model based Agents', 'Agent Memory', ['Agent Memory'], ['Agentic Memory', 'Large Language Models'], []),
    ('2404.15155', 'Adaptive Collaboration Strategy for LLMs in Medical Decision Making', 'Scientific and Medical Agents', ['Coordination', 'Scientific Discovery'], ['Multi-Agent Collaboration'], []),
    ('2404.18021', 'CRISPR-GPT: An LLM Agent for Automated Design of Gene-Editing Experiments', 'Scientific and Medical Agents', ['Scientific Discovery'], ['Large Language Models'], []),
    ('2405.02957', 'Agent Hospital: A Simulacrum of Hospital with Evolvable Medical Agents', 'Scientific and Medical Agents', ['Scientific Discovery'], [], []),
    ('2405.16334', "Devil's Advocate: Anticipatory Reflection for LLM Agents", 'Agent Architectures', ['Self-Reflection'], ['Self-Reflection', 'Large Language Models'], []),
    ('2405.16533', 'Chain of Tools: Large Language Model is an Automatic Multi-tool Learner', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2406.15341', 'GenoTEX: An LLM Agent Benchmark for Automated Gene Expression Data Analysis', 'Benchmarks and Evaluation', ['Data Science Agents', 'Evaluation', 'Scientific Discovery'], ['Benchmarking', 'Large Language Models'], ['GenoTEX']),
    ('2407.06813', 'Richelieu: Self-Evolving LLM-Based Agents for AI Diplomacy', 'Simulation and Society', ['Self-Improvement', 'Simulation'], ['Large Language Models'], []),
    ('2407.09811', 'CellAgent: An LLM-driven Multi-Agent Framework for Automated Single-cell Data Analysis', 'Multi-Agent Systems', ['Data Science Agents', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2407.18901', 'AppWorld: A Controllable World of Apps and People for Benchmarking Interactive Coding Agents', 'Benchmarks and Evaluation', ['Evaluation'], ['Benchmarking'], ['AppWorld']),
    ('2408.01875', 'Re-Invoke: Tool Invocation Rewriting for Zero-Shot Tool Retrieval', 'Retrieval and RAG', ['Retrieval-Augmented Generation', 'Tool Use'], ['Retrieval-Augmented Generation', 'Tool Learning'], []),
    ('2408.04168', 'Perceive, Reflect, and Plan: Designing LLM Agent for Goal-Directed City Navigation without Instructions', 'Embodied and Vision', ['Embodied Agents', 'Self-Reflection', 'Planning'], ['Self-Reflection', 'Large Language Models'], []),
    ('2409.07703', 'DSBench: How Far Are Data Science Agents to Becoming Data Science Experts?', 'Scientific and Medical Agents', ['Data Science Agents', 'Scientific Discovery'], [], ['DSBench']),
    ('2409.14457', 'Large Model Based Agents: State-of-the-Art, Cooperation Paradigms, Security and Privacy, and Future Trends', 'Safety and Security', ['Privacy', 'Security'], [], []),
    ('2409.14826', 'ToolPlanner: A Tool Augmented LLM for Multi Granularity Instructions with Path Planning and Feedback', 'Tool Use and APIs', ['Planning', 'Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2409.18807', 'LLM With Tools: A Survey', 'Tool Use and APIs', ['Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2410.03439', 'ToolGen: Unified Tool Retrieval and Calling via Generation', 'Retrieval and RAG', ['Retrieval-Augmented Generation', 'Tool Use', 'Scientific Discovery'], ['Retrieval-Augmented Generation', 'Tool Learning'], []),
    ('2410.09097', 'Recent advancements in LLM Red-Teaming: Techniques, Defenses, and Ethical Considerations', 'Safety and Security', ['Safety and Security'], ['Large Language Models'], []),
    ('2410.15686', 'NetSafe: Exploring the Topological Safety of Multi-agent Networks', 'Safety and Security', ['Distributed Systems', 'Safety', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2412.00300', 'PlanCritic: Formal Planning with Human Feedback', 'Agent Architectures', ['Alignment', 'Self-Reflection', 'Human-AI Interaction'], [], []),
    ('2412.06435', 'Simulating Human-like Daily Activities with Desire-driven Autonomy', 'Simulation and Society', ['Agent Societies', 'Human-AI Interaction', 'Simulation'], ['Simulation'], []),
    ('2412.15266', 'On the Structural Memory of LLM Agents', 'Agent Memory', ['Agent Memory'], ['Agentic Memory', 'Large Language Models'], []),
    ('2412.20138', 'TradingAgents: Multi-Agents LLM Financial Trading Framework', 'Multi-Agent Systems', ['Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2412.20977', 'UnrealZoo: Enriching Photo-realistic Virtual Worlds for Embodied AI', 'Embodied and Vision', ['Embodied Agents'], [], ['UnrealZoo']),
    ('2501.04227', 'Agent Laboratory: Using LLM Agents as Research Assistants', 'Scientific and Medical Agents', ['Scientific Discovery'], ['Search', 'Large Language Models'], []),
    ('2501.05707', 'Multiagent Finetuning: Self Improvement with Diverse Reasoning Chains', 'Multi-Agent Systems', ['Finetuning', 'Self-Improvement', 'Reasoning'], ['Fine-Tuning', 'Multi-Agent Collaboration'], []),
    ('2501.06322', 'Multi-Agent Collaboration Mechanisms: A Survey of LLMs', 'Multi-Agent Systems', ['Coordination', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2501.14249', "Humanity's Last Exam", 'Agent Systems', ['Human-AI Interaction'], [], ["Humanity's Last Exam"]),
    ('2502.06776', 'Towards Internet-Scale Training For Agents', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2502.08586', 'Commercial LLM Agents Are Already Vulnerable to Simple Yet Dangerous Attacks', 'Safety and Security', ['Security'], ['Large Language Models'], []),
    ('2502.08599', 'SPeCtrum: A Grounded Framework for Multidimensional Identity Representation in LLM-Based Agent', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2502.11127', 'G-Safeguard: A Topology-Guided Security Lens and Treatment on LLM-based Multi-agent Systems', 'Embodied and Vision', ['Distributed Systems', 'Security', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2502.11404', 'ToolCoder: A Systematic Code-Empowered Tool Learning Framework for Large Language Models', 'Software Agents', ['Software Agents', 'Tool Use'], ['Tool Learning', 'Large Language Models'], []),
    ('2502.12110', 'A-MEM: Agentic Memory for LLM Agents', 'Agent Memory', ['Agent Memory'], ['Agentic Memory', 'Large Language Models'], []),
    ('2502.12575', 'DemonAgent: Dynamically Encrypted Multi-Backdoor Implantation Attack on LLM-based Agent', 'Safety and Security', ['Planning', 'Security'], ['Large Language Models'], []),
    ('2502.13172', 'Unveiling Privacy Risks in LLM Agent Memory', 'Agent Memory', ['Privacy', 'Agent Memory'], ['Agentic Memory', 'Large Language Models'], []),
    ('2502.14276', 'STeCa: Step-level Trajectory Calibration for LLM Agent Learning', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2502.14529', 'CORBA: Contagious Recursive Blocking Attacks on Multi-Agent Systems Based on Large Language Models', 'Safety and Security', ['Security', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2503.01935', 'MultiAgentBench: Evaluating the Collaboration and Competition of LLM agents', 'Benchmarks and Evaluation', ['Coordination', 'Evaluation', 'Multi-Agent Systems'], ['Benchmarking', 'Multi-Agent Collaboration', 'Large Language Models'], ['AgentBench', 'MultiAgentBench']),
    ('2503.02197', 'ATLaS: Agent Tuning via Learning Critical Steps', 'Agent Architectures', ['Finetuning', 'Self-Reflection'], ['Fine-Tuning'], []),
    ('2503.03459', 'Unified Mind Model: Reimagining Autonomous Agents in the LLM Era', 'Agent Architectures', ['Agent Architectures'], ['Large Language Models'], []),
    ('2503.13657', 'Why Do Multi-Agent LLM Systems Fail?', 'Multi-Agent Systems', ['Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2503.15478', 'SWEET-RL: Training Multi-Turn LLM Agents on Collaborative Reasoning Tasks', 'Reinforcement Learning', ['Coordination', 'Reasoning'], ['Reinforcement Learning', 'Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2503.18102', 'AgentRxiv: Towards Collaborative Autonomous Research', 'Scientific and Medical Agents', ['Coordination', 'Scientific Discovery'], ['Search', 'Multi-Agent Collaboration'], []),
    ('2503.24047', 'Towards Scientific Intelligence: A Survey of LLM-based Scientific Agents', 'Scientific and Medical Agents', ['Scientific Discovery'], ['Large Language Models'], []),
    ('2504.07461', 'Achilles Heel of Distributed Multi-Agent Systems', 'Multi-Agent Systems', ['Distributed Systems', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2504.15585', 'A Comprehensive Survey in LLM(-Agent) Full Stack Safety: Data, Training and Deployment', 'Safety and Security', ['Safety'], ['Large Language Models'], []),
    ('2504.16736', 'A Survey of AI Agent Protocols', 'Tool Use and APIs', ['Agent Protocols'], [], []),
    ('2504.18243', 'DualRAG: A Dual-Process Approach to Integrate Reasoning and Retrieval for Multi-Hop Question Answering', 'Retrieval and RAG', ['Retrieval-Augmented Generation', 'Reasoning'], ['Retrieval-Augmented Generation'], []),
    ('2504.21034', 'SAGA: A Security Architecture for Governing AI Agentic Systems', 'Safety and Security', ['Security'], [], []),
    ('2505.04997', 'Foam-Agent: Towards Automated Intelligent CFD Workflows', 'Agent Architectures', ['Agent Architectures'], [], []),
    ('2505.11717', 'WebInject: Prompt Injection Attack to Web Agents', 'Safety and Security', ['Web Agents', 'Security'], ['Prompt Engineering'], []),
    ('2505.13044', 'Cognitive AI Memory: A Framework for More Human-like Memory in LLMs', 'Agent Memory', ['Human-AI Interaction', 'Agent Memory'], ['Agentic Memory'], []),
    ('2505.17512', 'Probe by Gaming: A Game-based Benchmark for Assessing Conceptual Knowledge in LLMs', 'Benchmarks and Evaluation', ['Simulation', 'Evaluation'], ['Benchmarking'], []),
    ('2505.18223', 'IDA-Bench: Evaluating LLMs on Interactive Guided Data Analysis', 'Embodied and Vision', ['Data Science Agents', 'Evaluation'], ['Benchmarking'], []),
    ('2505.19255', 'VTool-R1: VLMs Learn to Think with Images via Reinforcement Learning on Multimodal Tool Use', 'Reinforcement Learning', ['Vision-Language Models', 'Reinforcement Learning', 'Reasoning'], ['Reinforcement Learning', 'Tool Learning'], []),
    ('2505.20718', 'VLM Can Be a Good Assistant: Enhancing Embodied Visual Tracking with Self-Improving Vision-Language Models', 'Embodied and Vision', ['Embodied Agents', 'Vision-Language Models', 'Self-Improvement'], ['Large Language Models'], []),
    ('2506.02951', 'Adaptive Graph Pruning: A Task-Adaptive Multi-Agent Collaboration Framework', 'Multi-Agent Systems', ['Coordination', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2506.04135', 'macOSWorld: An Interactive Benchmark for GUI Agents', 'Embodied and Vision', ['Web Agents', 'Evaluation'], ['Benchmarking'], ['macOSWorld']),
    ('2506.04651', 'Agents of Change: Self-Evolving LLM Agents for Strategic Planning', 'Agent Architectures', ['Self-Improvement', 'Planning'], ['Large Language Models'], []),
    ('2506.08292', 'From Debate to Equilibrium: Belief-Driven Multi-Agent LLM Reasoning via Bayesian Nash Equilibrium', 'Multi-Agent Systems', ['Debate', 'Reasoning', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2506.08379', 'Reinforcing Large Language Model Reasoning through Multi-Agent Reflection', 'Multi-Agent Systems', ['Self-Reflection', 'Reasoning', 'Multi-Agent Systems'], ['Self-Reflection', 'Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2506.11791', 'SEC-bench: Automated Benchmarking of LLM Agents on Real-World Software Security Tasks', 'Software Agents', ['Software Agents', 'Security', 'Evaluation'], ['Benchmarking', 'Large Language Models'], []),
    ('2506.13131', 'AlphaEvolve: A coding agent for scientific and algorithmic discovery', 'Scientific and Medical Agents', ['Scientific Discovery'], [], []),
    ('2506.20743', 'A Survey of AI for Materials Science: Foundation Models, LLM Agents, Datasets, and Tools', 'Tool Use and APIs', ['Tool Use', 'Scientific Discovery'], ['Tool Learning', 'Large Language Models'], []),
    ('2506.21805', 'CitySim: Modeling Urban Behaviors and City Dynamics with Large-Scale LLM-Driven Agent Simulation', 'Simulation and Society', ['Agent Societies', 'Simulation'], ['Simulation', 'Large Language Models'], []),
    ('2507.02097', 'The Future is Agentic: Definitions, Perspectives, and Open Challenges of Multi-Agent Recommender Systems', 'Multi-Agent Systems', ['Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2507.02825', 'Establishing Best Practices for Building Rigorous Agentic Benchmarks', 'Benchmarks and Evaluation', ['Evaluation'], ['Benchmarking'], []),
    ('2507.12806', 'MCPEval: Automatic MCP-based Deep Evaluation for AI Agent Models', 'Benchmarks and Evaluation', ['API Calling', 'Evaluation'], ['Model Context Protocol', 'Benchmarking'], ['MCPEval']),
    ('2507.21035', 'GenoMAS: A Multi-Agent Framework for Scientific Discovery via Code-Driven Gene Expression Analysis', 'Software Agents', ['Software Agents', 'Scientific Discovery', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2507.21504', 'Evaluation and Benchmarking of LLM Agents: A Survey', 'Benchmarks and Evaluation', ['Evaluation'], ['Benchmarking', 'Large Language Models'], []),
    ('2507.22034', 'UserBench: An Interactive Gym Environment for User-Centric Agents', 'Agent Architectures', ['Agent Architectures'], [], ['UserBench']),
    ('2507.23633', 'MemoCue: Empowering LLM-Based Agents for Human Memory Recall via Strategy-Guided Querying', 'Agent Memory', ['Human-AI Interaction', 'Agent Memory'], ['Agentic Memory', 'Large Language Models'], []),
    ('2508.02085', 'SE-Agent: Self-Evolution Trajectory Optimization in Multi-Step Reasoning with LLM-Based Agents', 'Agent Architectures', ['Reasoning'], ['Large Language Models'], []),
    ('2508.04652', 'LLM Collaboration With Multi-Agent Reinforcement Learning', 'Reinforcement Learning', ['Reinforcement Learning', 'Coordination', 'Multi-Agent Systems'], ['Reinforcement Learning', 'Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2508.07407', 'A Comprehensive Survey of Self-Evolving AI Agents: A New Paradigm Bridging Foundation Models and Lifelong Agentic Systems', 'Agent Architectures', ['Self-Improvement'], [], []),
    ('2508.09549', 'CS-Agent: LLM-based Community Search via Dual-agent Collaboration', 'Multi-Agent Systems', ['Coordination'], ['Search', 'Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2508.12800', 'Atom-Searcher: Enhancing Agentic Deep Research via Fine-Grained Atomic Thought Reward', 'Scientific and Medical Agents', ['Reasoning', 'Scientific Discovery'], ['Search'], []),
    ('2508.12981', 'Analyzing Information Sharing and Coordination in Multi-Agent Planning', 'Multi-Agent Systems', ['Planning', 'Coordination', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2508.15126', 'aiXiv: A Next-Generation Open Access Ecosystem for Scientific Discovery Generated by AI Scientists', 'Scientific and Medical Agents', ['Scientific Discovery'], [], []),
    ('2508.15760', 'LiveMCP-101: Stress Testing and Diagnosing MCP-enabled Agents on Challenging Queries', 'Tool Use and APIs', ['API Calling'], ['Model Context Protocol'], ['LiveMCP-101']),
    ('2508.16665', 'Trust but Verify! A Survey on Verification Design for Test-time Scaling', 'Safety and Security', ['Test-Time Scaling', 'Verification'], ['Verification'], []),
    ('2508.17196', 'BudgetThinker: Empowering Budget-aware LLM Reasoning with Control Tokens', 'Agent Architectures', ['Reasoning'], ['Large Language Models'], []),
    ('2508.17674', 'Attacking LLMs and AI Agents: Advertisement Embedding Attacks Against Large Language Models', 'Safety and Security', ['Security'], ['Large Language Models'], []),
    ('2508.18669', 'MUA-RL: Multi-turn User-interacting Agent Reinforcement Learning for agentic tool use', 'Reinforcement Learning', ['Reinforcement Learning', 'Tool Use'], ['Reinforcement Learning', 'Tool Learning'], []),
    ('2508.19828', 'Memory-R1: Enhancing Large Language Model Agents to Manage and Utilize Memories via Reinforcement Learning', 'Agent Memory', ['Agent Memory', 'Reinforcement Learning'], ['Agentic Memory', 'Reinforcement Learning', 'Large Language Models'], []),
    ('2508.21104', 'PVPO: Pre-Estimated Value-Based Policy Optimization for Agentic Reasoning', 'Reinforcement Learning', ['Reinforcement Learning', 'Reasoning'], ['Reinforcement Learning'], []),
    ('2508.21148', 'A Survey of Scientific Large Language Models: From Data Foundations to Agent Frontiers', 'Scientific and Medical Agents', ['Scientific Discovery'], ['Large Language Models'], []),
    ('2508.21365', 'Think in Games: Learning to Reason in Games via Reinforcement Learning with Large Language Models', 'Reinforcement Learning', ['Simulation', 'Reinforcement Learning', 'Reasoning'], ['Reinforcement Learning', 'Large Language Models'], []),
    ('2508.21475', 'MMSearch-Plus: A Simple Yet Challenging Benchmark for Multimodal Browsing Agents', 'Embodied and Vision', ['Vision-Language Models', 'Evaluation'], ['Search', 'Benchmarking'], ['MMSearch-Plus']),
    ('2508.21720', 'PosterForest: Hierarchical Multi-Agent Collaboration for Scientific Poster Generation', 'Scientific and Medical Agents', ['Coordination', 'Scientific Discovery', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2508.21803', 'Automated Clinical Problem Detection from SOAP Notes using a Collaborative Multi-Agent LLM Architecture', 'Scientific and Medical Agents', ['Coordination', 'Scientific Discovery', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2509.01211', 'Web Fraud Attacks on LLM-driven Multi-Agent Systems', 'Safety and Security', ['Web Agents', 'Security', 'Multi-Agent Systems'], ['Multi-Agent Collaboration', 'Large Language Models'], []),
    ('2509.02547', 'The Landscape of Agentic Reinforcement Learning for LLMs: A Survey', 'Reinforcement Learning', ['Reinforcement Learning'], ['Reinforcement Learning'], []),
    ('2509.06235', 'PillagerBench: A Competitive Multi-Agent Benchmark for Evaluating LLM-based Agents in Minecraft', 'Benchmarks and Evaluation', ['Evaluation', 'Multi-Agent Systems'], ['Benchmarking', 'Multi-Agent Collaboration', 'Large Language Models'], ['PillagerBench']),
    ('2509.11939', 'PrivWeb: Unobtrusive and Content-aware Privacy Protection For Web Agents', 'Safety and Security', ['Web Agents', 'Privacy'], [], []),
    ('2509.14278', 'Beyond Data Privacy: New Privacy Risks for Large Language Models', 'Safety and Security', ['Privacy'], ['Large Language Models'], []),
    ('2509.17488', 'Privacy in Action: Towards Realistic Privacy Mitigation and Evaluation for LLM-Powered Agents', 'Benchmarks and Evaluation', ['Privacy', 'Evaluation'], ['Benchmarking', 'Large Language Models'], []),
    ('2510.03215', 'Cache-to-Cache: Direct Semantic Communication Between Large Language Models', 'Agent Systems', ['Agent Systems'], ['Large Language Models'], []),
    ('2510.07172', 'NewtonBench: Benchmarking Generalizable Scientific Law Discovery in LLM Agents', 'Benchmarks and Evaluation', ['Evaluation', 'Scientific Discovery'], ['Benchmarking', 'Large Language Models'], ['NewtonBench']),
    ('2510.07841', 'Self-Improving LLM Agents at Test-Time', 'Agent Architectures', ['Test-Time Scaling', 'Self-Improvement'], ['Large Language Models'], []),
    ('2510.08529', 'CoMAS: Co-Evolving Multi-Agent Systems via Interaction Rewards', 'Multi-Agent Systems', ['Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
    ('2510.16079', 'EvolveR: Self-Evolving LLM Agents through an Experience-Driven Lifecycle', 'Agent Architectures', ['Self-Improvement'], ['Large Language Models'], []),
    ('2510.20733', 'Thought Communication in Multiagent Collaboration', 'Multi-Agent Systems', ['Coordination', 'Reasoning', 'Multi-Agent Systems'], ['Multi-Agent Collaboration'], []),
]

#: Editorial scope notes, written for this demo (never quotations).
SUMMARIES: dict[str, str] = {
    '2501.04227':
        'Orchestrates research assistants as agents with structured roles and artefacts, evaluating how '
        'far an automated pipeline can carry a machine-learning research project.',
    '2501.05707':
        'Fine-tunes models on their own diverse reasoning chains and merges the results, showing multi- '
        'agent style gains can be distilled back into a single model.',
    '2501.06322':
        'Surveys multi-agent collaboration mechanisms along axes of structure, strategy, coordination '
        'and communication, and compares when collaboration actually pays off.',
    '2501.13956':
        'Builds memory for agentic search that compresses past reasoning trajectories, so later steps '
        'can reuse earlier conclusions without repeating retrieval.',
    '2502.11127':
        'Argues that the topology of a multi-agent network determines its exposure to adversarial '
        'infection, and proposes graph-based treatment to contain propagation.',
    '2502.12110':
        'Presents an agentic memory system in which the agent maintains an evolving network of linked '
        'notes, deciding for itself what to store and how to connect it, rather than using a fixed '
        'memory schema.',
    '2502.13172':
        'Shows that agent memory systems can leak private user information through benign-looking '
        'retrieval queries, and evaluates defensive memory designs.',
    '2503.01935':
        'Introduces a benchmark that scores LLM agents on collaboration and competition, with '
        'milestones, coordination metrics and a graph-based evaluation of multi-agent interaction.',
    '2503.13657':
        'Analyses a large set of multi-agent LLM system traces and finds failures cluster into '
        'recurring categories, with coordination and verification gaps dominating over raw model '
        'capability.',
    '2503.24047':
        'Surveys LLM-based scientific agents and argues that tool integration and verification, not '
        'model scale, are the binding constraints for scientific use.',
    '2504.16736':
        'Surveys emerging protocols for agent interoperability, comparing message formats, capability '
        'discovery and trust assumptions across proposed standards such as MCP and A2A.',
    '2505.04997':
        'Builds a domain agent for computational fluid dynamics that automates simulation setup and '
        'analysis, an example of agents applied to specialist engineering workflows.',
    '2505.13044':
        'Proposes a memory framework intended to mirror human memory organisation, with separate '
        'episodic and semantic stores and a consolidation routine, to reduce context overflow in long '
        'conversations.',
    '2505.16067':
        'Empirically studies memory management strategies in LLM agents and finds that naive '
        'accumulation of memories can reduce accuracy, so how memory is curated matters more than '
        'whether it exists.',
    '2505.19255':
        'Trains a multimodal model with reinforcement learning to call image tools, extending tool '
        'learning from text-only pipelines to visual reasoning.',
    '2506.07398':
        'Introduces a hierarchical memory architecture for multi-agent systems that captures inter- '
        'agent interaction history and distils reusable insights, improving coordination on long- '
        'horizon collaborative tasks.',
    '2506.08292':
        'Casts multi-agent debate as a game and derives equilibrium belief updates, aiming to make '
        'multi- agent reasoning converge instead of drifting toward confident but unsupported answers.',
    '2506.13131':
        'Uses an evolutionary loop in which a coding agent searches and mutates programs to make new '
        'scientific and algorithmic discoveries, scored by automated evaluators.',
    '2506.20743':
        'Surveys AI for materials science, spanning foundation models, agent systems, datasets and the '
        'tooling gaps that separate models from usable laboratory workflows.',
    '2507.02097':
        'Argues that recommendation should move from single-shot ranking toward agentic and multi-agent '
        'architectures, and surveys evaluation and safety consequences of that shift.',
    '2507.07957':
        'Proposes a multi-component memory system for agents that stores and retrieves different kinds '
        'of experience separately, arguing that a single undifferentiated memory degrades retrieval '
        'quality.',
    '2507.12806':
        'Provides an MCP-based evaluation harness for agents, automating task generation and scoring '
        'for model-context-protocol tool use.',
    '2508.07407':
        'Reviews self-evolving agents that generate or curate their own training experience, and '
        'compares the feedback loops they rely on.',
    '2508.08997':
        'Gives each agent in a multi-agent system an intrinsic, role-conditioned memory stream, so '
        'agents build task-specific memory rather than sharing one global log.',
    '2508.19828':
        'Trains an agent with reinforcement learning to decide when to add, update, delete and retrieve '
        'memories, showing that learned memory management improves downstream task performance.',
    '2508.21365':
        'Trains reasoning in game environments with reinforcement learning and transfers the resulting '
        'behaviour to text reasoning tasks.',
    '2510.03215':
        'Transfers intermediate representations directly between models instead of exchanging text, '
        'reporting efficiency gains for collaborations between heterogeneous models.',
    '2510.04851':
        'Builds modular memory for multi-agent systems from reusable memory units that agents assemble '
        'at run time, so the same components can serve different role assignments.',
    '2510.20733':
        'Explores letting agents exchange abstract thoughts rather than full messages, arguing that '
        'communication bandwidth rather than model capability can bottleneck collaboration.',
    '2401.05268':
        'Builds an agent that learns to plan from its own trajectories using self-generated '
        'supervision, reducing dependence on hand-designed multi-agent workflows.',
    '2401.10019':
        'Introduces a benchmark for whether agents recognise the safety risks of actions they are about '
        'to take, and finds current models often fail to flag them.',
    '2402.01030':
        'Shows that having language agents emit executable code as actions is more accurate and '
        'composable than emitting structured natural-language or JSON actions.',
    '2402.01586':
        'Proposes a safety framework that checks agent plans before execution, combining rule-based '
        'verification with model-based risk judgement.',
    '2402.05120':
        'Reports that repeatedly sampling from a single LLM and aggregating answers, without any multi- '
        'agent scaffolding, gives consistent gains over elaborate multi-agent role designs.',
    '2402.08567':
        'Demonstrates that a single adversarial image can be propagated through a network of multimodal '
        'agents, amplifying a local jailbreak exponentially as agents share messages.',
    '2403.00839':
        'Organises tools into a graph so agents can navigate from general to specific capabilities when '
        'the tool set is very large.',
    '2404.11584':
        'Compares emerging agent architectures and argues that planning, tool wiring and memory choices '
        'matter more than the choice of underlying model.',
    '2404.13501':
        'Surveys memory mechanisms for LLM-based agents, organising the design space into memory '
        'sources, storage forms, writing and retrieval operations, and evaluation practice.',
    '2404.16130':
        'Builds a knowledge graph from a document collection and uses community-level summaries to '
        'answer global, corpus-wide questions that local vector retrieval cannot.',
    '2404.18021':
        'Applies an LLM agent to the design of gene-editing experiments, retrieving domain constraints '
        'and proposing guide designs for expert review.',
    '2405.02957':
        'Simulates a hospital of interacting medical agents that learn from accumulated cases, as a '
        'testbed for medical decision support without exposing real patients.',
    '2405.16533':
        'Has the agent compose tools into executable chains rather than choosing single tools, so '
        'multi- step tasks are handled by generated pipelines.',
    '2408.01875':
        'Rewrites tool queries at invocation time, letting agents use unseen APIs without fine-tuning.',
    '2410.03439':
        'Generates tool calls directly as tokens so retrieval and invocation are handled by one model, '
        'removing a separate retrieval stage.',
    '2412.20138':
        'Composes specialised trading agents into a pipeline with structured debate and risk control, '
        'and reports portfolio behaviour over evaluation periods.',
    '2412.20977':
        'Provides realistic virtual worlds for embodied agents, reporting that current agents transfer '
        'poorly from static benchmarks to interactive visual environments.',
    '2302.04761':
        'Trains a model to decide for itself when to call external tools and how to incorporate their '
        'outputs into its text, using self-supervised generated training data.',
    '2303.11366':
        'Improves agent performance by storing verbal self-reflections after failure and feeding them '
        'back on the next attempt, without updating model weights.',
    '2304.07590':
        'Has a model play multiple collaborating roles in one generation process, removing the need to '
        'instantiate separate agent processes.',
    '2305.10601':
        'Generalises chain-of-thought to deliberate search over partial solutions, letting the model '
        'evaluate and backtrack among candidate reasoning branches.',
    '2305.14325':
        'Shows that generating several reasoning chains with separate model instances and converging on '
        'a shared answer improves factuality and arithmetic accuracy over single-agent decoding.',
    '2305.15334':
        'Fine-tunes a model to select among thousands of real APIs, and adds retrieval-aware training '
        "so the model's API choice adapts to documentation changes.",
    '2306.03314':
        'Surveys early multi-agent LLM collaboration and proposes a taxonomy of conversation roles and '
        'orchestration styles.',
    '2306.05685':
        'Shows that strong LLMs can judge answer quality at near-human agreement, while documenting '
        'position, verbosity and self-enhancement biases that limit automated judging.',
    '2307.07924':
        'Assigns software-team roles to conversational agents and lets them produce a multi-file '
        'application from a single specification, exposing coordination failure modes.',
    '2307.16789':
        'Fine-tunes a model on instruction data collected through depth-first search over a large real- '
        'world API corpus so it can plan and execute multi-step API calls.',
    '2308.00352':
        'Assigns specialised software-engineering roles to agents and coordinates them through an '
        'explicit standard-operating-procedure pipeline, improving structured output over chat-only '
        'multi-agent baselines.',
    '2308.03688':
        'Evaluates language models as agents across several interactive environments and finds a large '
        'gap between their chat ability and their ability to act over long horizons.',
    '2308.08155':
        'Proposes a framework in which multiple conversational agents solve tasks by exchanging '
        'messages, with configurable agents, tools and execution patterns including code-driven '
        'workflows.',
    '2309.02427':
        'Proposes a cognitive-architecture vocabulary for language agents — modular memory, decision '
        'procedures and action spaces — and uses it to compare existing agent designs.',
    '2310.02170':
        'Lets agents recruit and dismiss collaborators dynamically during a task, forming teams sized '
        'to the problem rather than fixed in advance.',
    '2310.08560':
        'Treats the language model as an operating system process: a fixed context window is '
        'virtualised over an external memory store that the model reads and writes through explicit '
        'function calls.',
    '2310.11511':
        'Trains a model to retrieve adaptively and to critique its own generations with reflection '
        'tokens, so support and relevance decisions are made per query.',
    '2311.12983':
        'Defines a benchmark of real-world assistant questions that require tool use, browsing and '
        'multi- step reasoning, where human respondents score far above current assistants.',
    '2312.13010':
        'Uses multiple agents to write, test and repair code iteratively, improving pass rates on '
        'program-synthesis benchmarks.',
    '2210.03629':
        'Interleaves reasoning traces with tool actions so the model can gather and react to external '
        'evidence while solving knowledge-intensive tasks.',
    '2108.07258':
        'Frames foundation models as a general-purpose technology and analyses their opportunities and '
        'emergent risks, including homogenisation and misuse potential.',
    '2112.04359':
        'Catalogues ethical and social risks from increasingly capable language models and argues that '
        'risk assessment must combine technical and social analysis.',
    '2004.07213':
        'Proposes mechanisms for verifiable claims about AI systems, arguing that trustworthy '
        'deployment needs technical evidence combined with institutional practices.',
    '2005.11401':
        'Combines a parametric sequence model with a dense retriever over a document index, showing '
        'that retrieval improves knowledge-intensive tasks and factual grounding.',
}

#: Curated citation lineage inside the corpus (paper -> paper it builds on).
LINEAGE: list[tuple[str, str]] = [
    ('2303.11366', '2210.03629'),  # Reflexion: Language Agents with Verbal R -> ReAct: Synergizing Reasoning and Acting 
    ('2305.10601', '2201.11903'),  # Tree of Thoughts: Deliberate Problem Sol -> Chain-of-Thought Prompting Elicits Reaso
    ('2203.11171', '2201.11903'),  # Self-Consistency Improves Chain of Thoug -> Chain-of-Thought Prompting Elicits Reaso
    ('2205.11916', '2201.11903'),  # Large Language Models are Zero-Shot Reas -> Chain-of-Thought Prompting Elicits Reaso
    ('2210.03629', '2201.11903'),  # ReAct: Synergizing Reasoning and Acting  -> Chain-of-Thought Prompting Elicits Reaso
    ('2310.11511', '2005.11401'),  # Self-RAG: Learning to Retrieve, Generate -> Retrieval-Augmented Generation for Knowl
    ('2404.16130', '2005.11401'),  # From Local to Global: A Graph RAG Approa -> Retrieval-Augmented Generation for Knowl
    ('2308.08155', '2210.03629'),  # AutoGen: Enabling Next-Gen LLM Applicati -> ReAct: Synergizing Reasoning and Acting 
    ('2308.00352', '2308.08155'),  # MetaGPT: Meta Programming for A Multi-Ag -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2307.16789', '2302.04761'),  # ToolLLM: Facilitating Large Language Mod -> Toolformer: Language Models Can Teach Th
    ('2305.15334', '2302.04761'),  # Gorilla: Large Language Model Connected  -> Toolformer: Language Models Can Teach Th
    ('2306.05685', '2005.14165'),  # Judging LLM-as-a-Judge with MT-Bench and -> Language Models are Few-Shot Learners
    ('2402.05120', '2201.11903'),  # More Agents Is All You Need -> Chain-of-Thought Prompting Elicits Reaso
    ('2310.08560', '2005.14165'),  # MemGPT: Towards LLMs as Operating System -> Language Models are Few-Shot Learners
    ('2302.04761', '2005.14165'),  # Toolformer: Language Models Can Teach Th -> Language Models are Few-Shot Learners
    ('1810.04805', '1706.03762'),  # BERT: Pre-training of Deep Bidirectional -> Attention Is All You Need
    ('2005.14165', '1706.03762'),  # Language Models are Few-Shot Learners -> Attention Is All You Need
    ('2106.09685', '1810.04805'),  # LoRA: Low-Rank Adaptation of Large Langu -> BERT: Pre-training of Deep Bidirectional
    ('2203.02155', '2005.14165'),  # Training language models to follow instr -> Language Models are Few-Shot Learners
    ('2103.01955', '1707.06347'),  # The Surprising Effectiveness of PPO in C -> Proximal Policy Optimization Algorithms
    ('1502.05477', '1312.5602'),  # Trust Region Policy Optimization -> Playing Atari with Deep Reinforcement Le
    ('1707.06347', '1502.05477'),  # Proximal Policy Optimization Algorithms -> Trust Region Policy Optimization
    ('1710.10903', '1609.02907'),  # Graph Attention Networks -> Semi-Supervised Classification with Grap
    ('1706.02275', '1707.06347'),  # Multi-Agent Actor-Critic for Mixed Coope -> Proximal Policy Optimization Algorithms
    ('2502.12110', '2310.08560'),  # A-MEM: Agentic Memory for LLM Agents -> MemGPT: Towards LLMs as Operating System
    ('2506.07398', '2502.12110'),  # G-Memory: Tracing Hierarchical Memory fo -> A-MEM: Agentic Memory for LLM Agents
    ('2503.13657', '2308.08155'),  # Why Do Multi-Agent LLM Systems Fail? -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2503.13657', '2308.00352'),  # Why Do Multi-Agent LLM Systems Fail? -> MetaGPT: Meta Programming for A Multi-Ag
    ('2501.06322', '2308.08155'),  # Multi-Agent Collaboration Mechanisms: A  -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2503.01935', '2308.08155'),  # MultiAgentBench: Evaluating the Collabor -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2305.14325', '2201.11903'),  # Improving Factuality and Reasoning in La -> Chain-of-Thought Prompting Elicits Reaso
    ('2505.16067', '2404.13501'),  # How Memory Management Impacts LLM Agents -> A Survey on the Memory Mechanism of Larg
    ('2404.13501', '2310.08560'),  # A Survey on the Memory Mechanism of Larg -> MemGPT: Towards LLMs as Operating System
    ('2508.19828', '2310.08560'),  # Memory-R1: Enhancing Large Language Mode -> MemGPT: Towards LLMs as Operating System
    ('2505.13044', '2310.08560'),  # Cognitive AI Memory: A Framework for Mor -> MemGPT: Towards LLMs as Operating System
    ('2212.06817', '1706.03762'),  # RT-1: Robotics Transformer for Real-Worl -> Attention Is All You Need
    ('2501.13956', '2310.08560'),  # Zep: A Temporal Knowledge Graph Architec -> MemGPT: Towards LLMs as Operating System
    ('2402.01030', '2210.03629'),  # Executable Code Actions Elicit Better LL -> ReAct: Synergizing Reasoning and Acting 
    ('2309.02427', '2210.03629'),  # Cognitive Architectures for Language Age -> ReAct: Synergizing Reasoning and Acting 
    ('2304.03442', '2210.03629'),  # Generative Agents: Interactive Simulacra -> ReAct: Synergizing Reasoning and Acting 
    ('2408.01875', '2305.15334'),  # Re-Invoke: Tool Invocation Rewriting for -> Gorilla: Large Language Model Connected 
    ('2405.16533', '2307.16789'),  # Chain of Tools: Large Language Model is  -> ToolLLM: Facilitating Large Language Mod
    ('2410.03439', '2302.04761'),  # ToolGen: Unified Tool Retrieval and Call -> Toolformer: Language Models Can Teach Th
    ('2501.04227', '2308.00352'),  # Agent Laboratory: Using LLM Agents as Re -> MetaGPT: Meta Programming for A Multi-Ag
    ('2508.07407', '2303.11366'),  # A Comprehensive Survey of Self-Evolving  -> Reflexion: Language Agents with Verbal R
    ('2510.20733', '2305.14325'),  # Thought Communication in Multiagent Coll -> Improving Factuality and Reasoning in La
    ('2506.08292', '2305.14325'),  # From Debate to Equilibrium: Belief-Drive -> Improving Factuality and Reasoning in La
    ('2412.20138', '2308.08155'),  # TradingAgents: Multi-Agents LLM Financia -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2404.18021', '2304.05376'),  # CRISPR-GPT: An LLM Agent for Automated D -> ChemCrow: Augmenting large-language mode
    ('2505.04997', '2402.01030'),  # Foam-Agent: Towards Automated Intelligen -> Executable Code Actions Elicit Better LL
    ('2507.12806', '2504.16736'),  # MCPEval: Automatic MCP-based Deep Evalua -> A Survey of AI Agent Protocols
    ('2405.02957', '2308.08155'),  # Agent Hospital: A Simulacrum of Hospital -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2507.02097', '2308.08155'),  # The Future is Agentic: Definitions, Pers -> AutoGen: Enabling Next-Gen LLM Applicati
    ('2510.03215', '2310.08560'),  # Cache-to-Cache: Direct Semantic Communic -> MemGPT: Towards LLMs as Operating System
    ('2508.08997', '2506.07398'),  # Intrinsic Memory Agents: Heterogeneous M -> G-Memory: Tracing Hierarchical Memory fo
    ('2510.04851', '2506.07398'),  # Legomem: Modular Procedural Memory for M -> G-Memory: Tracing Hierarchical Memory fo
    ('2503.24047', '2404.18021'),  # Towards Scientific Intelligence: A Surve -> CRISPR-GPT: An LLM Agent for Automated D
    ('2306.03314', '2304.03442'),  # Multi-Agent Collaboration: Harnessing th -> Generative Agents: Interactive Simulacra
    ('2506.13131', '2402.01030'),  # AlphaEvolve: A coding agent for scientif -> Executable Code Actions Elicit Better LL
    ('2304.07590', '2210.03629'),  # Self-collaboration Code Generation via C -> ReAct: Synergizing Reasoning and Acting 
]

#: (arxiv_id, claim text, polarity, direction, assertive, curated tension targets)
CLAIMS: list[tuple] = [
    ('2402.05120',
     'Increasing the number of sampled agents consistently improves task performance across '
     'reasoning benchmarks.'
     , 'positive', 'increase', True, ['2503.13657']),
    ('2503.13657',
     'Increasing the number of agents does not improve task performance when coordination failures '
     'dominate the pipeline.'
     , 'negative', 'increase', True, ['2402.05120']),
    ('2502.12110',
     'Agentic memory that the model organises itself improves long-horizon task performance across '
     'sessions.'
     , 'positive', 'increase', True, ['2505.16067']),
    ('2505.16067',
     'Uncurated agentic memory degrades long-horizon task performance, so accumulation is not a '
     'substitute for curation.'
     , 'negative', 'increase', True, ['2502.12110']),
    ('2302.04761',
     'Self-supervised tool-use training lets a language model decide when to call tools and '
     'improves downstream answer quality.'
     , 'positive', 'increase', True, ['2408.01875']),
    ('2408.01875',
     'Rewriting the tool query at invocation time lets a language model reach unseen APIs without '
     'self-supervised tool-use training.'
     , 'negative', 'decrease', True, ['2302.04761']),
    ('2402.08567',
     'Adversarial propagation through a network of multimodal agents amplifies exponentially, '
     'outpacing model-level defences.'
     , 'negative', 'increase', True, ['2502.11127']),
    ('2502.11127',
     'Topology-guided treatment reduces adversarial propagation, so amplification in multi-agent '
     'networks is not inevitable.'
     , 'positive', 'decrease', True, ['2402.08567']),
    ('2502.13172',
     'Agent memory systems do not isolate private user content, so ordinary retrieval paths leak '
     'it.'
     , 'negative', 'increase', True, ['2402.01586']),
    ('2402.01586',
     'Checking agent memory before execution prevents the private data leaks that model-level '
     'refusal misses.'
     , 'positive', 'decrease', True, ['2502.13172']),
    ('2210.03629',
     'Interleaving reasoning with tool actions improves agent success on long-horizon knowledge- '
     'intensive tasks.'
     , 'positive', 'increase', True, ['2308.03688']),
    ('2308.03688',
     'A large gap separates conversational ability from long-horizon acting, so agent frameworks '
     'do not transfer their chat competence to long tasks.'
     , 'negative', 'increase', True, ['2210.03629']),
    ('2508.19828',
     "Training an agent's memory operations with reinforcement learning improves later answers "
     'over static retrieval.'
     , 'positive', 'increase', True, ['2505.16067']),
    ('2505.16067',
     'Uncurated memory accumulation degrades later answers, so stored experience is not a reliable '
     'substitute for retrieval.'
     , 'negative', 'increase', True, ['2508.19828']),
    ('2310.08560',
     'Virtual context management over an external memory store lets a fixed-context model handle '
     'unbounded documents.'
     , 'positive', 'increase', True, []),
    ('2303.11366',
     'Verbal self-reflection stored in episodic memory improves agent success on later attempts.'
     , 'positive', 'increase', True, []),
    ('2308.00352',
     'Assigning specialised roles to agents through a standard-operating-procedure pipeline '
     'produces more reliable software artefacts than unstructured chat.'
     , 'positive', 'increase', True, []),
    ('2506.07398',
     'Hierarchical memory over inter-agent traces improves multi-agent coordination quality on '
     'long- horizon tasks.'
     , 'positive', 'increase', True, []),
    ('2305.14325',
     'Independent agents exchanging and revising answers improve factuality compared with a single '
     'model instance.'
     , 'positive', 'increase', True, []),
    ('2506.08292',
     'Casting multi-agent debate as a game makes belief updates converge instead of drifting '
     'toward confident unsupported answers.'
     , 'positive', 'increase', True, []),
    ('2402.01030',
     'Emitting executable code as agent action improves accuracy and composability over JSON-style '
     'actions.'
     , 'positive', 'increase', True, []),
    ('2309.02427',
     'A modular cognitive architecture with explicit memory and action spaces explains and '
     'improves language agent design.'
     , 'positive', 'increase', True, []),
    ('2103.01955',
     'PPO with a centralised critic is a strong, simple baseline for cooperative multi-agent '
     'games.'
     , 'positive', 'increase', True, []),
    ('1707.06347',
     'A clipped surrogate objective gives stable policy improvement across continuous and discrete '
     'control.'
     , 'positive', 'increase', True, []),
    ('2201.11903',
     'Prompting with intermediate reasoning steps substantially improves arithmetic and symbolic '
     'reasoning.'
     , 'positive', 'increase', True, []),
    ('2203.11171',
     'Sampling diverse reasoning paths and taking the majority answer improves chain-of-thought '
     'accuracy.'
     , 'positive', 'increase', True, []),
    ('2205.11916',
     'Appending a task-agnostic prompt elicits reasoning on arithmetic and symbolic tasks.'
     , 'positive', 'increase', True, []),
    ('2305.10601',
     'Deliberate search over reasoning branches improves success on problems that require '
     'exploration.'
     , 'positive', 'increase', True, []),
    ('2005.11401',
     'Retrieval over an explicit document index improves knowledge-intensive generation and '
     'factual grounding.'
     , 'positive', 'increase', True, []),
    ('2404.16130',
     'Community-level graph summaries answer corpus-wide questions that local retrieval cannot '
     'reach.'
     , 'positive', 'increase', True, []),
    ('2310.11511',
     'Adaptive retrieval with self-critique improves factuality over always-retrieve pipelines.'
     , 'positive', 'increase', True, []),
    ('2501.13956',
     'Compressing past search trajectories into memory lets later reasoning steps reuse earlier '
     'conclusions.'
     , 'positive', 'increase', True, []),
    ('2507.07957',
     'Separating memory by modality and purpose improves recall for long-running assistants '
     'compared with one undifferentiated store.'
     , 'positive', 'increase', True, []),
    ('2510.04851',
     'Modular memory units that agents assemble at run time transfer across workflow automation '
     'tasks.'
     , 'positive', 'increase', True, []),
    ('2508.08997',
     'Role-conditioned intrinsic memory reduces the coordination overhead of a shared global '
     'memory log.'
     , 'positive', 'decrease', True, []),
    ('2304.03442',
     'Agents with memory, reflection and planning reproduce believable social behaviour in a '
     'simulated town.'
     , 'positive', 'increase', True, []),
    ('2505.13044',
     'Human-like memory organisation with a consolidation routine reduces context pressure in long '
     'conversations.'
     , 'positive', 'increase', True, []),
    ('2306.05685',
     'Strong language models judge answer quality at near-human agreement on dialog benchmarks.'
     , 'positive', 'increase', True, []),
    ('2311.12983',
     'Real-world assistant tasks remain far beyond current assistants compared with human '
     'respondents.'
     , 'negative', 'increase', True, []),
    ('2307.16789',
     'Search-generated API instruction data lets a model execute multi-step calls over a very '
     'large API corpus.'
     , 'positive', 'increase', True, []),
    ('2305.15334',
     'Retrieval-aware API fine-tuning adapts model behaviour to documentation changes at inference '
     'time.'
     , 'positive', 'increase', True, []),
    ('2405.16533',
     'Composing tools into executable chains lets agents handle multi-step tasks that single-tool '
     'selection misses.'
     , 'positive', 'increase', True, []),
    ('2401.10019',
     'Current language agents often fail to recognise the safety risks of actions they are about '
     'to perform.'
     , 'negative', 'increase', True, []),
    ('2506.13131',
     'An evolutionary loop over agent-written programs discovers new algorithmic improvements '
     'scored by evaluators.'
     , 'positive', 'increase', True, []),
    ('2503.24047',
     'Tool integration and verification, not model scale, are the binding constraints for '
     'scientific agents.'
     , 'negative', 'increase', True, []),
    ('2504.16736',
     'Agent interoperability requires agreed message and capability-discovery protocols, and '
     'current proposals diverge.'
     , 'negative', 'neutral', True, []),
    ('2503.01935',
     'Benchmarking multi-agent systems needs coordination and competition metrics beyond task '
     'success.'
     , 'positive', 'neutral', True, []),
    ('2405.02957',
     'Agents that accumulate case experience in a simulated hospital improve medical decision '
     'quality over static prompting.'
     , 'positive', 'increase', True, []),
    ('2412.20138',
     'A pipeline of specialised trading agents with structured debate produces measurable '
     'portfolio behaviour.'
     , 'positive', 'neutral', False, []),
    ('2108.07258',
     'Foundation models create broad capability gains and systemic risks that require coordinated '
     'mitigation.'
     , 'negative', 'increase', False, []),
    ('2112.04359',
     'Language-model harms arise from social context as much as from model behaviour, so '
     'mitigation must be socio-technical.'
     , 'negative', 'neutral', True, []),
]
