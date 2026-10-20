 v5 is adopted in the architecture and coordination docs, and D-11 
  to D-14 are recorded as approved. I've stopped there: Phase 2     
  hasn't started, and nothing was committed, pushed or filed. I sent
  no request to the Qwen server and changed no application code;    
  mxfix/mxissues are untouched.                                     
                                                                    
  D-12 filesystem check. XDG_STATE_HOME is unset, so the directory  
  resolves to ~/.local/state/mx-admin-agents/, which doesn't exist  
  yet. Its parent is on local ext4 with mode 0700, a suitable       
  filesystem for flock. I created nothing. Before the coordinator   
  relies on flock, it must refuse network or FUSE filesystems at    
  start-up and pass a two-process lock test; that test is planned   
  for Phase 2.                                                      
                                                                    
  Qwen profile (D-14). The exact v5 profile is in                   
  architecture/README.md §11.11:                                    
  - Model ID: Qwen3.6-35B-A3B-Q4_K_M.gguf.                          
  - Declared limits: 131,072 context, 16,384 output, 128,000 input. 
  - Input cap actually usable: at most 114,688 tokens (131,072 −    
    16,384), or 110,592 as a working cap after a 4,096-token safety 
    margin.                                                         
  - Settings: temperature 0.2, and three edit tools (apply-patch,   
    multi-find-replace, find-replace) that run on this host, not on 
    the server.                                                     
  - Declared but unvalidated: tool calling, vision, streaming and   
    thinking. Nothing relies on them until tested.                  
  - Endpoint: configured only at runtime (AGENT_QWEN_BASE_URL,      
    AGENT_QWEN_MODEL, optional AGENT_QWEN_API_KEY).                 
                                                                    
  I deliberately left the literal IP out of docs_agents/, as v5     
  §10.1.1 asks; it stays only in the prompt file.                   
                                                                    
  Two facts matter for the security review:                         
  - The endpoint is plain HTTP, so the default is that no source    
    code goes to it.                                                
  - It's the same host and port the backend's three enabled         
    qwen_translate configs already call over HTTP. It also shares an
    IP address with the GitLab remote; whether it's the same        
    machine is unknown.                                             
                                                                    
  Phase 1 gates under v5: docs checks pass 6/6. The specs cover     
  91/91 operations with no drift, all 30 tooling tests pass, and all
  8 service READMEs are present. The CLI tally was already resolved 
  from the underlying matrix: 41 covered + 2 via login + 3 broken + 
  38 gaps + 7 exceptions = 91.   

  Changes:                                                          
  - architecture/README.md:                                         
    - v5 header and the D-12 result.                                
    - "Local Qwen" statements marked as superseded.                 
    - New §11.11 on remote Qwen integration.                        
    - Decision register (§12) updated.                              
  - phase-status.md:                                                
    - v5 adoption record and handoff checks.                        
    - Decisions and blockers updated.                               
    - Phase 2/3/7 plans amended for remote Qwen: mocked only in     
      Phase 2, adapter tests in Phase 3.                            
  - One-line updates to docs_agents/README.md and api-contracts.md. 


  Remaining open decisions                                          
                                                                    
  None of these blocks the start of Phase 2.                        
                                                                    
  ID: D-4                                                           
  Decision: Confirm dev as the base for feature/* branches. D-11    
  implies it but doesn't state it.                                  
  Needed before: Phase 5                                            
  ────────────────────────────────────────                          
  ID: D-5                                                           
  Decision: When to fix the insecure auth defaults and other        
  security                                                          
  findings.                                                         
  Needed before: Before parallel or unattended runs                 
  ────────────────────────────────────────                          
  ID: D-9                                                           
  Decision: Regenerate the stale backend/docs/openapi.yaml, or mark 
  it superseded.                                                    
  Needed before: Optional                                           
  ────────────────────────────────────────                          
  ID: D-10                                                          
  Decision: Approve committing the bootstrap work, a prerequisite   
  for                                                               
  the D-11 PR. Review the Qwen IP in agents_prompt_v5.md before     
  those files leave this private repo.                              
  Needed before: D-11 PR; ideally before Phase 2 code          
  ────────────────────────────────────────                          
  ID: D-15                                                          
  Decision: Secure the Qwen endpoint's transport (TLS or a VPN      
  tunnel), or grant a documented plain-HTTP exception.              
  Needed before: Any live Qwen use with code                        
  ────────────────────────────────────────                          
  ID: D-16                                                          
  Decision: Which code and context may be sent to the remote model. 
  Needed before: Same as D-15                                       
  ────────────────────────────────────────                          
  ID: D-17                                                          
  Decision: Which local program drives Qwen and runs its edit tools 
  on this host. Candidates would be shortlisted in Phase 2 and      
  validated with mocks in Phase 3.                                  
  Needed before: Phase 3                                            
  ────────────────────────────────────────                          
  ID: D-18                                                          
  Decision: Whether the Qwen server needs an API key; if so, it     
  comes                                                             
  from a private secret store.                                      
  Needed before: Any live request                                   
                                                                    
  New blockers B-9 (plain HTTP) and B-10 (no Qwen runner chosen,    
  capabilities unvalidated) affect only Automation's use of Qwen.   
  When you're ready, say Proceed to Phase 2.                        
                                                 
                                                   