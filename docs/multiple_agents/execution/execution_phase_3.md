Phase 3 is done and every exit gate is met. I've stopped there:   
  Phase 4 has not started, nothing is committed, pushed or opened,  
  and nothing was sent to the remote Qwen server.                   
                                                                    
  Security issue — please rotate your Claude Code token. While      
  checking what a sandboxed process can see, my smoke probe read the
  real ~/.claude/.credentials.json and printed its first 90         
  characters. A prefix of your OAuth access token is therefore in   
  this session's transcript. Run claude auth logout, then log in    
  again. Since then, all tests use a fake home directory with fake  
  credentials, and the real-login check only asserts "logged in". 

  Implementation details                                            
                                                                    
  Nine new modules in scripts/feature_coordinator/:                 
  - sandbox.py: each runner and each gate runs inside bubblewrap.   
    - Each role can write only its own directories.                 
    - .git, other roles' trees and protected paths are read-only.   
    - The home directory and /tmp are private, and the coordinator's
      state directory is invisible.                                 
    - Untracked secret files like .env are hidden.                  
    - If bubblewrap is missing, nothing runs.                       
  - launchers.py: the real Claude Code and Codex launchers.         
    - Claude Code runs in --restricted mode, which never loads the  
      repo's bypassPermissions setting. It gets a generated tool    
      allow/deny list, no MCP servers and a budget cap.             
    - Codex keeps its own sandbox, nested inside ours, so the       
      commands it runs get no network.                              
    - Before launching, the installed CLI's --help is checked for   
      every required flag; bypass flags can never be emitted.       
  - supervisor.py: heartbeats, operator stop, timeouts, and SIGTERM 
    then SIGKILL of the whole sandbox. A crashed worker's sandbox   
    dies with it.                                                   
  - gates.py, lint_baseline.py: checks run offline against a        
    read-only checkout, with timeouts and logs. backend-tests is    
    reported as blocked until B-7 is fixed. Lint follows D-19:      
    existing Pylint findings are reported, new ones block.          
  - http_transport.py, capabilities.py: the minimal Qwen client     
    (D-17), with streaming, cancellation and capability probing. It 
    can only reach 127.0.0.1, so the remote server is unreachable.  
  - operations.py, commands.py: new CLI commands runners,           
    sandbox-check, gate, lint-report, activate, launch, stop,       
    recover and integrate. Each is a dry run unless given --apply.  
                                                                 
D-4 (dev as base branch) and D-5 (auth and debug defaults         
  untouched) are reflected in the config.                           
                                                                    
  Files created and modified (all uncommitted)                      
                                                                    
  - New: the nine modules above; 2 test helpers (a fake agent CLI   
    and a fake OpenAI-compatible server); 8 test files;             
    docs_agents/coordination/agent-ownership.md, operations.md and  
    backend-task-b7.md.                                             
  - Modified: 12 existing coordinator modules, 4 existing test      
    files, feature-workflow.md (new §17–§18),                       
    project-config.example.yaml, phase-status.md,                   
    docs_agents/README.md and docs_agents/automation/README.md.     
  - Outside Git: gate logs under                                    
    ~/.local/state/mx-admin-agents/logs/.           

    Tests executed and actual results                                 
                                                                    
  - Full suite: 294 tests collected; 290 passed and 4 opt-in tests  
    were skipped. 90 of the tests are new, and the concurrency-heavy
    ones were stable over repeated runs.                            
  - Quality checks: lint scores 10/10 for both code and tests, and  
    the docs check passes 6/6.                                      
  - On the live checkout (read-only, offline):                      
    - contract-check, docs-check and all five frontend checks       
      passed;                                                       
    - the lint baselines are 1 existing finding for Backend and 34  
      for the CLI, matching Phase 0;                                
    - backend-tests was blocked as intended;                        
    - the checkout was unchanged afterwards.                        
                                                                    
  Real Claude Code / Codex integration status                       
                                                                    
  - Verified without model calls: both installed CLIs (Claude Code  
    2.1.302, Codex 0.163.0) have every required flag, start inside  
    the sandbox, and see their login with read-only credentials.    
    Codex's own sandbox also works nested inside ours.              
  - Live smoke runs (your approval), each in a throwaway repo:      
    - Claude Code created backend/hello.txt. It had exactly 6 tools,
      no MCP servers and no permission denials; the run cost        
      $0.0225.                                                      
    - Codex created frontend/hello.txt with one command.            
    - Neither run wrote anything outside its role's directories.   

     Sandbox enforcement results                                       
                                                                    
  - Containment test: every role tried 19 kinds of write, including 
    other roles' trees, .git, protected paths, the repo root and a  
    generated file. Every outcome matched the path policy, with 0   
    mismatches. The root .env stayed invisible, a symlink escape was
    denied, and git commit was refused.                             
  - Parallel exit gate: two real worker processes, one Backend and  
    one Frontend, ran at the same time on one checkout. While both  
    were active:                                                    
    - a second Backend writer was rejected;                         
    - a branch switch was refused;                                  
    - a full-project session was refused.                           
                                                                    
    Each worker then landed exactly one single-role commit, and a   
    third role's half-done file was not captured.                   
  - Lifecycle: stop, timeout, SIGTERM, and SIGKILL followed by      
    recovery and resume all behave as designed. A detached          
    grandchild process dies with its sandbox, and an alive worker   
    that stops heartbeating is reported, never reclaimed.           
                                                                    
  Known limitations                                                 
                                                                    
  - B-7 (the known blocker): I traced it to                         
    test_delete_item_image_last_remaining_is_allowed, without       
    touching your content. That test calls item_images() outside its
    settings patch, so it creates lock files in the real content/   
    tree. .gitignore hides them, and the failure is tolerated when  
    the tree is read-only. The live tree currently has no such      
    files. The required Backend fix is written up in                
    backend-task-b7.md.                                             
  - Credentials inside the sandbox: each runner's credentials are   
    readable inside its sandbox, which is how the token leak        
    happened. Claude's file tools are denied access to them, but a  
    test an agent writes could still read them.                     
  - Claude Code's network: its sandbox shares the host network,     
    because it needs its API.                                       
  - Unverified: OAuth token refresh with read-only credentials.     
  - Qwen: the remote server stays unreachable, and its declared     
    capabilities are unvalidated against it.                        
  - Not built yet: no live GitHub adapter (Phase 4).     

   Remaining architectural decisions                                 
                                                                    
  - D-20, credential handling: I recommend accepting the current    
    mitigations for supervised Phase 5, and moving credentials      
    outside the sandbox before any unattended runs.                 
  - D-21, runner network policy: I recommend keeping only the       
    command allowlist for now, then adding an egress-allowlisting   
    proxy.                                                          
  - D-22: approve updating docs_agents/architecture/ with the Phase 
    3 results. I didn't touch it because it is approval-required.   
  - D-23: the integration check set for Phase 5.                    
  - Still open: D-9 (the stale backend/docs/openapi.yaml), and D-15,
    D-16 and D-18, which must all be settled before any live Qwen   
    use.                                                            
  - Commit: how to commit this Phase 3 work.                        
                                                                    
  Please review and approve Phase 3. The detail is in               
  phase-status.md, agent-ownership.md and operations.md.                                                                         