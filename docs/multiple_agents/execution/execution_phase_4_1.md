The Projects extension is finished and every exit gate is met,    
  including a live test against Project #2 (35/35 checks). Project  
  #1 and Issues #1–#14 are unchanged, and nothing was committed,    
  pushed, merged or opened as a PR. I've stopped here; Phase 5 has  
  not started.                                                      
                                                                    
  Project #2 setup (feature.py github project setup --apply)        
                                                                    
  Your gh login already had the project scope, so nothing was needed
  from you. Nothing was deleted, and running setup again changes    
  nothing.                                                          
                                                                    
  Status options                                                    
  Created or reused: Backlog, Ready, In Progress, Blocked, Review,  
  Done. "Todo" was renamed to Backlog with its option ID kept, so   
  GitHub's "Item added to project" workflow still points at it. In  
  Progress and Done were reused unchanged.                          
  ────────────────────────────────────────                          
  Fields                                                            
  Created or reused: Agent (Coordination, Backend, CLI, Frontend,   
  Automation, Full Project), Feature ID, Blocked Reason (all        
  created)                                                          
  ────────────────────────────────────────       
    Views                                                             
  Created or reused: Kanban: your existing "View 2" board, already  
  grouped by Status, renamed. Active Tasks: a new board filtered to 
   Ready, In Progress, Blocked and Review. Both cards show the      
  three new fields; "View 1" is untouched.                          
                                                                    
  How the coordinator uses the board                                
                                                                    
  - Read-only picture: the board shows the local state and is never 
    read as input. A moved card or edited field starts, releases or 
    approves nothing; the next sync puts it back and reports it.    
  - Status follows the existing task states:                        
    - Review means the agent's commit landed and its checks are     
      running.                                                      
    - Done needs passing checks tied to a commit on the feature     
      branch.                                                       
    - The parent feature becomes Done only after its PR is merged.  
    - Blocked Reason shows the unmet dependency, the hold reason or 
      the failure.                                                  
  - Which Issues appear: features planned from now on are added     
    automatically. Older features need an explicit github project   
    associate FEATURE --apply.                                      
  - Locks: a worker running a task holds its role lock, so it never 
    contacts GitHub itself. A separate process updates In Progress  
    and Review instead.                                             
  - Project #1 protection: three layers — a configuration check, a  
    guard that refuses any write outside Project #2, and a test     
    tripwire.                                                       
  - New commands: feature.py github project inspect | validate |    
    setup | associate | sync | reconcile | active | task | test.    
    Writes need --apply.                                            
                                                                    
  Live test (two test Issues, both closed afterwards)               
                                                                    
  ┌────────────┬──────────────────────────────┬─────────────────┐   
  │   Issue    │          Board item          │  Final board    │   
  │            │                              │      state      │   
  ├────────────┼──────────────────────────────┼─────────────────┤   
  │ #15 parent │ PVTI_lAHOACEIPc4BndH-zhBbwdk │ Review /        │   
  │            │                              │ Coordination    │   
  ├────────────┼──────────────────────────────┼─────────────────┤   
  │ #16        │                              │                 │   
  │ Backend    │ PVTI_lAHOACEIPc4BndH-zhBbwo8 │ Done / Backend  │   
  │ task       │                              │                 │   
  └────────────┴──────────────────────────────┴─────────────────┘   
                                                                    
                                                                     Confirmed by reading the board back through GitHub at each step:  
  - Association: an existing Issue was left off the board until I   
    ran the explicit associate command. The new task Issue was added
    automatically.                                                  
  - Crash recovery: after a crash injected right after GitHub added 
    an item, the restarted coordinator found that item and did not  
    add a second one.                                               
  - Status flow: Backlog → Ready → Blocked (with the hold reason) → 
    Ready → In Progress while the agent ran → Review during its     
    checks → Done → Ready (re-opened) → Done.                       
  - Manual card moves: moving cards started nothing, and a held task
    moved to Ready still couldn't be claimed. The next sync         
    restored both.                                                  
  - Repeat and restart: a repeated sync wrote nothing, and a        
    reconcile in a new process found nothing to change.             
                                                                    
  Tests                                                             
                                                                    
  - Local: 429 passed, 18 skipped (the skipped ones are opt-in live 
    and host tests). Before this extension: 368 passed.             
  - Live: 6 passed, opt-in with FC_GITHUB_PROJECTS_LIVE=1.          
  - Other checks: docs check 6/6, lint 10.00/10, configuration      
    identical to the built-in one.                                  
                                                                    
  What surprised me                                                 
                                                                    
  GitHub's own "Auto-close issue" workflow is on for Project #2. It 
  closed #15 when I moved its card to Done, and closed #16 each time
  the coordinator set it Done. The coordinator reopened them,       
  because only it may close Issues, but each reopen adds a comment  
  and a drift report.                                               
                                                                    
  Files                                                             
                                                                    
  - New code: github_projects.py, projects_local.py,                
    project_sync.py, project_commands.py and live_projects.py in    
    scripts/feature_coordinator/.                                   
  - New tests: test_fc_github_projects.py, test_fc_project_sync.py, 
    test_fc_projects_live.py.                                       
  - Existing Phase 4 code: small, additive changes. The details are 
    in the phase-status record.                                     
  - Docs: new github-projects.md; updated github-integration.md,    
    feature-workflow.md, operations.md, phase-status.md and         
    project-config.example.yaml.                                    
  - Not touched: docs_agents/architecture/README.md and             
    docs_agents/README.md, which need your approval to edit. The    
    architecture page still says "No Projects v2 is required".    

     Limitations                                                       
                                                                    
  - GitHub's workflows change items a few seconds later; the next   
    sync restores them.                                             
  - Review shows only for the short time a task's checks run.       
  - The API can't choose which columns a filtered board hides.      
  - Board edits are last-writer-wins.                               
  - The coordinator still shares your account, so board edits are   
    never treated as approval.                                      
                                                                    
  What I need from you                                              
                                                                    
  1. D-28 (recommended): in Project #2 → Workflows, switch off      
     Auto-close issue and Item closed. Only the web UI can do this. 
  2. Approvals: Phase 4 is still awaiting your approval; your       
     extension instruction didn't state it. This extension also     
     awaits approval.                                               
  3. D-10 and D-22: how to commit the uncommitted Phase 4 and       
     extension work, and whether to update the two approval-required
     doc pages.                                                     
  4. D-29: I recommend leaving #11–#14 off the board.               
  5. Still open: D-20, D-21, D-23–D-27 and the B-7 backend fix.     
                                                                    
  Phase 5 readiness                                                 
                                                                    
  The board is set up and supports a real feature. Phase 5 still    
  needs:                                                            
  - your approval of Phase 4 and this extension;                    
  - D-24 (allowing GitHub writes for real features, not just test   
    ones) and D-25 (push and one PR);                               
  - the B-7 backend fix;                                            
  - an approved new Backend + CLI + Frontend feature.               
                                                                    
  Please review and approve before I start Phase 5.      
                                                                