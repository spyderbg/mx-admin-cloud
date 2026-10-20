 I've stopped before planning or launching anything. Your stop     
  condition "reusable credentials are exposed to agent code" is met 
  by the current setup, and the decision that would cover it (D-20) 
  has never been made. I've created no Issues, branch, coordinator  
  state or config changes. I'm on dev at c419c0c and the checkout is
  unchanged.                                                        
                                                                    
  Why I stopped:                                                    
  - Credentials (D-20): each runner's own login file                
    (~/.claude/.credentials.json, ~/.codex/auth.json) is mounted    
    read-only inside its sandbox                                    
    (scripts/feature_coordinator/launchers.py:164). Code an agent   
    runs, such as a test it writes and runs with python -m pytest,  
    can read it. Phase 3 demonstrated this with a real token.       
  - Network (D-21): Claude Code's sandbox shares the host network   
    (agent_network: True), so its allowed commands could reach the  
    internet. Codex commands are offline, and gates are offline and 
    read-only.                                                      
                                                                    
  phase-status.md lists D-20 and D-21 as open decisions "before     
  Phase 5". It recommends accepting both for supervised Phase 5 with
  the existing mitigations, but nobody has approved that. Your      
  instruction also asks me to confirm credentials are not readable, 
  which is false today.                                             
                                                                    
  What passed:                                                      
  - PRs #17, #18 and #19 are merged; dev is c419c0c and identical to
    what I validated earlier.                                       
  - The B-7 fix is on dev, the blocked-gates list is empty, and the 
    example config is identical to the built-in one. The coordinator
    regression result (455 passed) applies, since the tree is       
    unchanged.                                                      
  - No feature, claim or run is recorded, so all role locks are     
    free; the state folder is OK.                                   
  - Both runners are present with the required flags. Claude Code is
    now 2.1.303 (Phase 3 verified 2.1.302); Codex is 0.163.0. Qwen  
    stays disabled.                                                 
  - The containment self-test passed today in a disposable repo.    

                                                                     
  Decisions I need from you. The first two are needed before any    
  agent launches; the rest before the run can go as you specified:  
                                                                    
  1. D-20, credentials. Either accept them as they are for this     
     supervised pilot, or build credential isolation first.         
     Isolation means keeping the login outside the sandbox and      
     handing the runner short-lived access; that's a separate       
     coordinator task, and it changes what you asked me to confirm. 
  2. D-21, network. Either accept Claude Code's shared network with 
     the command allowlist for this pilot, or add an egress proxy   
     first (also a separate coordinator task).                      
  3. D-24, real Issues. The coordinator only writes GitHub Issues   
     for test features. Your feature needs github.write_scope:      
     all-features: one line each in                                 
     scripts/feature_coordinator/config.py and                      
     project-config.example.yaml. No narrower per-feature option    
     exists.                                                        
  4. Missing gates for your section 11:                             
     - CLI tests: there's no cli-tests gate, and the CLI has no     
       tests at all. A new gate definition is needed (pytest, which 
       the repo already uses), and the CLI task would add the first 
       tests.                                                       
     - Secret scanning: the coordinator has none.                   
     - Whole-feature scope check: each commit is checked against its
       role's paths, but checking the whole feature diff is planned 
       for Phase 6.                                                 
                                                                    
     I'd add the cli-tests gate and run the other two as recorded   
     checks at integration. Either way, this is a coordinator config
     change that needs your approval.                               
  5. Your untracked file.                                           
     docs/multiple_agents/tests/second_content_statistics.txt is a  
     flow diagram of this feature, written today at 01:09. The      
     coordinator's final integration step refuses to run with any   
     untracked file in the checkout, so it needs to be moved,       
     committed separately, or excluded locally (via                 
     .git/info/exclude) before then.                                
                                                                    
  Once you decide, I'll finish reading the workflow, GitHub and v5  
  prompt docs and check how content counting works. Then I'll       
  prepare the feature spec, and nothing goes to GitHub or the agents
  until those decisions are recorded.          