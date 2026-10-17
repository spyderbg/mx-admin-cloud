   Phase 2 is done, all its exit gates are met, and I've stopped     
  there. Phase 3 has not been started. Nothing was committed, pushed
  or opened, and no request went to the remote Qwen server.         
                                                                    
  Implemented components                                            
                                                                    
  The coordinator lives in scripts/feature_coordinator/ (standard   
  library plus PyYAML), with scripts/feature.py as the entry point. 
                                                                    
  Requirement: Feature and task state models                        
  Implementation: model.py, store.py: versioned JSON records,       
  legal-transition tables, idempotent re-application. A task can    
  only be marked verified with gate evidence tied to the current    
  branch tip. A feature can only be done after a merged PR.         
  ────────────────────────────────────────  
  Requirement: Dependencies with blocking/resuming                  
  Implementation: The task graph rejects cycles and unknown tasks.  
  Waiting tasks resume automatically, but a block placed by a human 
   is never lifted automatically. When the CLI or Frontend needs a  
  missing API, exactly one Backend task is created and reused, and  
  only the task that needs it waits. Re-opening a task re-blocks    
  the tasks that depend on it.                                      
  ────────────────────────────────────────                          
  Requirement: Role locks and atomic claiming                       
  Implementation: flock locks in a fixed order: coordinator,        
  project,                                                          
  role, git. One writer per role. Full-project work waits for       
  running writers and never interrupts them.                        
  ────────────────────────────────────────                          
  Requirement: Write-scope validation                               
  Implementation: Deny-by-default: blocks path traversal, symlink   
  escapes, symlinks into another role's tree, and generated files
  written by the wrong role. Protected paths, including
  mxfix/mxissues, can never be granted.
  ────────────────────────────────────────
  Requirement: Git serialization
  Implementation: Each commit includes only the files the agent
  declared: the tree is built in a private index, file hashes are
  checked twice, and the branch only moves forward from the
  expected commit. Branch create/switch is refused while any writer
   is active. The push is only planned, never run.
  ────────────────────────────────────────
  Requirement: Mock runners
  Implementation: Claude Code and Codex mocks, which never use
  bypass
  modes. An OpenAI-compatible Qwen runner with the exact v5 limits
  (hard input cap 114,688 tokens, working cap 110,592),
  capabilities off until validated, plain-HTTP (D-15) and
  code-disclosure (D-16) checks, and a live transport that is
  disabled.
  ────────────────────────────────────────
  Requirement: Mock GitHub
  Implementation: Labels are restricted to feature:*, issues are
  deduplicated, issues from the bug system are read-only, and there
   is one PR per feature/* branch.
  ────────────────────────────────────────
  Requirement: Dry-run CLI
  Implementation: doctor [--apply], config, providers, plan, status,
  
  demo.
  ────────────────────────────────────────
  Requirement: One feature, one branch, one PR
  Implementation: The demo produces one feature/items-export branch,
  
    3 commits, 4 verified tasks and exactly 1 PR (a second one is     
  refused). It stops at review, not done.                           
                                                                    
  While testing I found and fixed four problems:                    
  - Stranded tasks: a task could be left in-progress if its commit  
    was refused, or if the branch moved between running the checks  
    and marking it verified. Every exit now ends in a defined state.
  - Integration barrier: integration didn't wait for all writers to 
    stop. It now takes the project lock exclusively.                
  - Incomplete frontend gate: it ran only test:voices. test:content 
    and test:auth are added.                                        
  - Lint gates: backend-lint lacked the documented PYTHONPATH=src,  
    and there was no cli-lint. Both are now in place.     
  
  Files created and modified (all uncommitted)                      
                                                                    
  - New:                                                            
    - scripts/feature.py                                            
    - 16 modules in scripts/feature_coordinator/                    
    - 10 test files in scripts/tests/feature_coordinator/           
    - docs_agents/coordination/feature-workflow.md (the design: data
      model, state machines, locks, Git protocol, Qwen              
      offline/error table, gate list, safety model, limitations,    
      exit-gate evidence)                                           
    - docs_agents/coordination/project-config.example.yaml          
  - Modified: phase-status.md (Phase 2 record),                     
    docs_agents/README.md, docs_agents/automation/README.md. My own 
    path policy lists docs_agents/README.md as approval-required; I 
    updated it as plan step P2-7.                                   
  - Outside Git: ~/.local/state/mx-admin-agents/, created empty by  
    doctor --apply. It is on ext4 with mode 0700. You can remove it 
    with rm -r.                                                     
  - Unchanged: backend/, cli/, frontend/, content/, data/, mxfix,   
    mxissues, .env, and the auth and debug defaults.                
                                                                    
  Tests executed and results                                        
                                                                    
  - Full suite: pytest -p no:cacheprovider scripts/tests gives 204  
    passed (30 Phase 1 + 174 new). The new tests were stable across 
    5 repeated runs. They use temporary repositories and state      
    directories only.                                               
  - What they cover:                                                
    - claim races between processes;                                
    - recovery after a worker is killed with SIGKILL, plus zombie,  
      PID-reuse and reboot detection;                               
    - three processes committing in parallel;                       
    - refusal to commit around another agent's staged or unfinished 
      files;                                                        
    - rejection of FUSE and network filesystems for state;          
    - every Qwen error path;                                        
    - an independence check confirming no imports from mxfix,       
      mxissues or the backend.                                      
  - Docs and lint: check_docs_agents.py passes 6/6. pylint scores   
    10.00/10 for both the package and the tests.                    
  - Real state directory: doctor --apply passed the two-process     
    flock test on the real ~/.local/state location.                 
                                                          
    
    Remaining limitations                                             
                                                                    
  - Runners are mocks, and there is no OS containment yet.          
  - The live Qwen transport is disabled. There is no HTTP client,   
    streaming, partial tool-call handling or apply-patch.           
  - There is no live GitHub adapter, push or PR.                    
  - All tests use simulated checks. backend-tests would write into  
    the real content/ (B-7).                                        
  - Token counts are estimates (bytes ÷ 3).                         
  - Heartbeats are recorded but not monitored.                      
                                                                    
  Decisions requiring approval                                      
                                                                    
  - Phase 2 review, and how to commit it. My recommendation is one  
    reviewed commit on agents.                                      
  - D-17 (Qwen runner): I recommend the coordinator's own tool loop.
    The alternatives are Codex with a custom provider, or OpenCode; 
    neither is verified.                                            
  - D-19 (new): which checks block integration. On the unchanged    
    code, backend-lint exits 8 (10.00/10 with one message) and      
    cli-lint exits 28 (9.62/10, 34 messages). If either is a        
    blocking gate, every feature fails until those messages are     
    fixed or the rule is relaxed.                                   
  - Still open: D-4 (base branch dev), D-5 (security priority), D-9,
    and D-15/D-16/D-18 (Qwen transport, disclosure and auth), which 
    must be settled before any live Qwen request. 
  
  Recommended Phase 3 plan                                          
                                                                    
  The full plan, steps P3-0 to P3-7, is in phase-status.md:         
  1. Approve Phase 2; decide D-5 and D-17; schedule the Backend fix 
     for B-7.                                                       
  2. Real claude/codex launchers, with settings generated into the  
     state directory rather than the repo.                          
  3. bwrap containment (repo read-only, role folders writable, .git 
     read-only, private /tmp), with tests that writes outside the   
     allowed paths fail.                                            
  4. Worker lifecycle: heartbeat, cancel, and                       
     launch/stop/resume/recover commands.                           
  5. Real gate execution, with timeouts and logs.                   
  6. Backend and Frontend writing at the same time in a throwaway   
     clone, never the live checkout.                                
  7. The Qwen HTTP client, tested against a local fake server first;
     a live smoke test only after D-15, D-16, D-18 and your         
     approval.                                                      
  8. Ownership and operations docs.    