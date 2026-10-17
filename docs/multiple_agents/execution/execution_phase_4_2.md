Phase 4 finalization is done and all ten validation items passed. 
  Nothing is committed or pushed, no PR was opened, and Phase 5 has 
  not started.                                                      
                                                                    
  You should know about two writes I made to GitHub:                
  - Active Tasks filter (D-29): I changed it on the real Project #2.
  - D-28 behavior check: I temporarily reopened and re-closed test  
    Issues #15 and #16. Both ended exactly as they started.         
                                                                    
  No other Issue was touched, and Project #1 was never written.     
                                                                    
  Validation results                                                
                                                                    
  #: 1                                                              
  Check: Project #2 configured                                      
  Result: The project's identity checks out, the project scope is   
  present, and every option, field and view exists. validate says   
  READY.                                                            
  ────────────────────────────────────────                          
  #: 2                                                              
  Check: Project #1 unchanged                                       
  Result: A full snapshot is byte-identical to the one taken at the 
  start of the session. Checked before my writes, after them, and   
  again at the end.                                                 
  ────────────────────────────────────────                          
  #: 3                                                              
  Check: Status sync                                                
  Result: #15 shows Review / Coordination and #16 shows Done /      
  Backend. Both match the local state exactly (read-only comparison 
   against the live test's records). Board tests: 42 passed.        
  ────────────────────────────────────────                          
  #: 4                                                              
  Check: Done no longer closes the Issue                            
  Result: Both workflows are disabled. Behavior check: #16's card   
  moved to Done and the Issue stayed open for 45 s. Closing #15     
  left its card at Review; in the morning's live test, the same     
  close set it to Done.                                             
  ────────────────────────────────────────                          
  #: 5                                                              
  Check: Moving a card can't start work                             
  Result: No module that claims tasks, holds locks, launches agents,
                                                                    
  runs gates or commits ever reads the board. Tests and the live    
  run's card-move steps agree.                                      
  ────────────────────────────────────────                          
  #: 6                                                              
  Check: Finished test tasks off Active Tasks                       
  Result: GitHub's own evaluation of the view returns 0 items.      
  Kanban                                                            
  still shows #15 and #16. Before the change, Active Tasks showed   
  #15.
    ────────────────────────────────────────                          
  #: 7                                                              
  Check: Regression tests                                           
  Result: 430 passed, 18 skipped (all opt-in live or host tests).   
  Lint 10.00/10, docs check 6/6, sandbox self-test passed, OpenAPI  
  check 91/91.                                                      
  ────────────────────────────────────────                          
  #: 8                                                              
  Check: Credential isolation                                       
  Result: Nothing secret was opened or printed. The gh config,      
  ~/.ssh, the runner credential files and .env all exist on the     
  host but are invisible inside all five role sandboxes. Only 12    
  allowlisted environment variables reach the sandboxes, and zero   
  token-shaped strings appear in the changes or the coordinator     
  state.                                                            
  ────────────────────────────────────────                          
  #: 9                                                              
  Check: B-7 (backend test writes into content/)                    
  Result: Still open and unchanged on every branch, including dev,  
  the Phase 5 base. The backend-tests gate stays blocked against    
  real checkouts. Recommendation: an approved, test-only Backend    
  fix into dev before Phase 5.                                      
  ────────────────────────────────────────                          
  #: 10                                                             
  Check: Phase 5 prerequisites and security limits                  
  Result: Written up in phase-status.md.  
                                                                      
  Project #2 now                                                    
                                                                    
  - Status options: Backlog, Ready, In Progress, Blocked, Review,   
    Done.                                                           
  - Fields: Agent, Feature ID, Blocked Reason.                      
  - Views:                                                          
    - Kanban shows every item.                                      
    - Active Tasks now shows only open Issues that are Ready, In    
      Progress, Blocked or Review (is:open status:Ready,"In         
      Progress",Blocked,Review).                                    
    - View 1 is unchanged.                                          
  - Workflows: "Auto-close issue" and "Item closed" are disabled.   
    Four others are still on:                                       
    - "Pull request linked to issue" and "Pull request merged" would
      move the parent's card in Phase 5; the coordinator puts it    
      back.                                                         
    - "Item added to project" and "Auto-add sub-issues to project"  
      are harmless.                                                 
  - Test Issues: #11–#14 stay off the board. #15 and #16 stay in    
    GitHub, closed, for audit.
                                                                        
  What changed in the files                                         
                                                                    
  - Code: one new helper in project_sync.py now builds the Active   
    Tasks filter (used by setup, the local board and validate), plus
    one new test.                                                   
  - D-22 docs: the architecture page no longer says "No Projects v2 
    is required", marks the Phase 3 results verified, and its       
    decision register is current. The docs index links the board    
    page.                                                           
  - Status lines: these pages now say approved: github-projects.md, 
    github-integration.md, operations.md, feature-workflow.md,      
    task-lifecycle.md, agent-ownership.md (Phase 3) and the         
    automation README.                                              
  - phase-status.md: a new finalization record, with the change     
    inventory, the commit plan and the Phase 5 prerequisites.       
                                                                    
                                                                    
  Uncommitted changes (48 files)                                    
                                                                    
  ┌────────────┬────────────────────────────────────────────────┐   
  │   Group    │                     Files                      │   
  ├────────────┼────────────────────────────────────────────────┤   
  │ Phase 4    │ 5 new modules, 11 modified (github_api,        │   
  │ code       │ issue_sync and config among them)              │   
  ├────────────┼────────────────────────────────────────────────┤   
  │ Projects   │ 5 new modules, plus small hooks in 9 Phase 4   │   
  │ code       │ modules (including gitops.py)                  │   
  ├────────────┼────────────────────────────────────────────────┤   
  │ Tests      │ 4 new and 1 modified for Phase 4; 3 new for    │   
  │            │ the board                                      │   
  ├────────────┼────────────────────────────────────────────────┤   
  │ Docs       │ 11 new (github-integration, task-lifecycle, 7  │   
  │            │ templates, github-projects), 8 modified        │   
  └────────────┴────────────────────────────────────────────────┘   
                                                                    
  Your 6 untracked files in docs/multiple_agents/execution/ (saved  
  copies of my phase reports) are left out of the plan; whether to  
  commit them is your call.                                         
                                                                    
  Proposed commits                                                  
                                                                    
  Your existing commits already cover the first three groups you    
  asked for, so I recommend leaving them as they are:               
  - 12183e0: Phase 0/1 docs and tooling                             
  - 8454e63: Phase 2 coordinator                                    
  - b3f3691: Phase 3 runners and sandbox                            
                                                                    
  Two new commits would go on top:                                  
  - C4, "Add GitHub Issue coordination and task dependencies        
    (Phase 4)": all 37 Phase 4 files in their Phase 4 versions. 18  
    of them were later changed by the Projects work too. I rebuilt  
    their Phase 4 versions by undoing that work's recorded edits;   
    replaying the edits forward reproduces today's files byte for   
    byte.                                                           
  - C5, "Add GitHub Projects board sync and finalize Phase 4":      
    everything else, at its final version.                          
                                                                    
  I rehearsed this in throwaway clones:                             
  - Each commit passes the tests, lint and docs check on its own:   
    367 for C4 and 429 for C5. Each count is one lower than in the  
    real checkout because one test needs the checkout's FUSE disk.  
  - When I edited a file after review, the script refused before    
    committing anything.                                  
                                                                    
  The plan and its files are in                                     
  ~/.local/state/mx-admin-agents/commit-plan-2026-10-17/. On        
  approval, it runs with:                                           
  bash ~/.local/state/mx-admin-agents/commit-plan-2026-10-17/commit_
  plan.sh /media/spyder/_Projects/MarXman/mx-admin-cloud            
  - Safety checks: it only stages files and never touches the       
    working tree. It stops if any file changed after your review,   
    and it never pushes.                                            
  - Undo: git reset --mixed b3f3691 reverses it before a push.      
                                                                    
  Still blocking Phase 5                                            
                                                                    
  1. Approve and run this commit plan.                              
  2. Before any push:                                               
     - review the literal Qwen address in the already-committed     
       agents_prompt_v5.md;                                         
     - decide what to do with your execution/ notes;                
     - then push and open the bootstrap PR into dev (D-11). That    
       needs its own approval.                                      
  3. B-7: the Backend test fix lands in dev, then the backend-tests 
     gate is unblocked.                                             
  4. D-23 (Phase 5 gate set), D-24 (GitHub writes for real          
     features), D-25 (push and one PR).                             
  5. D-20 and D-21 (runner credentials and network) for supervised  
     runs.                                                          
  6. An approved new Backend + CLI + Frontend feature, and an       
     explicit Phase 5 authorization.                                
  7. Optional: D-26 (a separate GitHub identity for the             
     coordinator), switching off the two pull-request workflows, and
     D-15/16/18 only if Qwen will be used.                          
                                                                    
  Until then, these limits stay in force:                           
  - GitHub writes are limited to test features, and no push, PR or  
    merge happens.                                                  
  - Project #1 is never written.                                    
  - Board edits and GitHub comments never authorize anything.       
  - No requests go to Qwen.                                         
  - The app's default settings and mxfix/mxissues stay untouched.   
                                                                    
  Please review and approve the commit plan, or tell me what to     
  change. I'll stop here.                                                                                                   