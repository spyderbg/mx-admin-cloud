Ето го целия текс от изпълнението на фаза 1 
ve adopted v4 in the documentation and stopped before Phase 2.  
  Nothing was committed, pushed or filed on GitHub. The only files I
  changed are under docs_agents/; no application code,              
  mxfix/mxissues, protected data or root config was touched.        
                                                                    
  CLI coverage counts. My Phase 1 summary left out one category:    
  covered indirectly, which is 2 operations. POST /api/v1/login and 
  POST /api/v1/cli-login have no CLI command, but the CLI's login   
  flow calls them. The matrix already listed them and summed to 91; 
  only my one-line summary dropped them. I recounted all 91         
  operations from the generated specs and the CLI's call sites and  
  got 41 + 2 + 3 + 38 + 7 = 91. cli-coverage.md now shows "43       
  covered = 41 by commands + 2 by the login flow", so the numbers   
  add up as printed.                                                
                                                                    
  Updated architectural decisions                                   
                                                                    
  The register is in architecture/README.md §12, with the full v4   
  design in §11.                                                    
  - Approved:                                                       
    - V4-1 to V4-12: your 12 points.                                
    - D-1 and D-2: confirmed.                                       
    - D-3: extended with a separate lock that serializes all Git    
      changes.                                                      
  - Resolved by v4:                                                 
    - D-6: v4 §3 now says who owns the previously unassigned paths. 
    - D-7: the old fixer-permission question no longer applies.     
  - New open decisions:                                             
    - D-11: how the bootstrap work reaches the base branch for      
      features.                                                     
    - D-12: where coordinator state and lock files live.            
    - D-13: package name and location for the new coordinator.      
    - D-14: whether and how to validate a local Qwen runner for the 
      automation role.                                              
  - Still open from before: D-4, D-5, D-9, D-10.                    
                                                                    
  Documentation changes                                             
                                                                    
  - architecture/README.md:                                         
    - Ownership updated to v4.                                      
    - The mxfix/mxissues section is now marked "Superseded by v4"   
      and kept for history.                                         
    - A new table lists build and test outputs that could collide in
      a shared checkout.                                            
    - New §11 covers the v4 design: the two separate systems, how   
      features and tasks move through states, per-role runners,     
      locking, a Git coordinator that alone writes to Git, how API  
      dependencies unblock waiting work, the GitHub adapter, and    
      recovery.                                                     
  - phase-status.md: rewritten for v4. The Phase 0 and Phase 1      
    records are kept, the Phase 2–7 plan is replaced, and the old v3
    plan is kept under "Superseded".                                
  - Smaller edits:                                                  
    - cli-coverage.md: the corrected summary.                       
    - api-contracts.md: D-2 marked approved, and contract checks    
      must now pass on the shared feature branch.                   
    - automation/README.md: the bug workflow is listed as "observe  
      only".                                                        
    - discovery/README.md: v4 notes added; the evidence itself is   
      unchanged.                                                    
    - The root index and four component READMEs: header details     
      only.                                   

  Phase 1 verification status                                       
                                                                    
  Verified. I re-ran its gates on agents/e2ddd9c:                   
  - check_docs_agents.py passes 6/6.                                
  - The OpenAPI drift check is up to date: 9 specs covering 91      
    operations.                                                     
  - The tooling tests pass, 30/30.                                  
  - mxfix/mxissues are untouched.                                   
  - Both users.db checksums are unchanged.                          
                                             

  Revised Phase 2 plan                                              
                                                                    
  Phase 2 designs the new feature coordinator and builds a minimal  
  tested skeleton, with no real agent runs and no live GitHub.      
  Everything stays independent of mxfix/mxissues, and Git tests run 
  only in temporary repositories.                                   
  - P2-1: feature-workflow.md, covering the feature and task model, 
    allowed state changes, claims, the one-branch/one-PR policy,    
    approvals, and when a waiting task is unblocked.                
  - P2-2: a proposal for the stored record formats, plus            
    project-config.example.yaml.                                    
  - P2-3: a catalog of the existing generic checks a feature must   
    pass.                                                           
  - P2-4: a read-only check of which permission flags claude and    
    codex actually support (no model calls).                        
  - P2-5: a standard-library skeleton in
    scripts/feature_coordinator/. It includes flock locks, a
    deny-by-default path policy, and a single serialized commit
    path. That path commits from a file list without touching the
    shared Git index, and refuses to move the branch if HEAD has
    changed. A mock GitHub tracker, mock runners and a CLI that
    defaults to dry-run complete it. 
  - P2-6: tests covering:
    - Locking: state changes, atomic claims, a second same-role
      writer rejected, release when a process dies.
    - Path policy: traversal and symlink writes rejected.
    - Git: parallel commits without capturing each other's files, a
      branch switch rejected while writers run.
    - Mock feature: CLI blocked on a Backend contract and then
      resumed; exactly one PR candidate.
    - Independence: a test that no mxfix/mxissues/backend imports
      exist.
  - Exit gate: a mock feature with Backend, CLI and Frontend tasks
    runs on one branch under the one-PR policy, with no calls into
    the bug workflow.
    
  Remaining blockers                                                
                                                                    
  - B-1: no OpenAPI validator is installed, so specs get structural 
    checks only.                                                    
  - B-2: branch protection isn't available on this private repo's   
    plan, and no CI runs tests.                                     
  - B-3 (narrower now): this only affected mxissues. The new adapter
    can use your logged-in gh, which has repo scope.                
  - B-4: Docker and Playwright aren't installed here, so browser e2e
    can't run.                                                      
  - B-5: the GitHub-to-GitLab mirror is failing.                    
  - B-6: the claude/codex permission flags, and how to override the 
    repo's bypassPermissions setting from a launcher, are           
    unverified.                                                     
  - B-7: a backend test writes lock files into the real content/    
    tree (F-5); it needs a Backend fix before parallel runs.        
  - B-8: the bootstrap work exists on no branch a feature could     
    start from.                                                     
                                                                    
  On the positive side, bwrap sandboxing works unprivileged on this 
  host, and the kernel's Landlock module is active. Either can      
  provide the Phase 3 write containment.                            
                                                                    
  To start Phase 2, I need decisions on two points:                 
  - D-12: I recommend keeping coordinator state outside the repo, on
    ext4 under $XDG_STATE_HOME/mx-admin-agents/. The checkout sits  
    on a fuseblk mount, and an in-repo directory would also need a  
    root .gitignore change.                                         
  - D-13: I propose scripts/feature_coordinator/ with a             
    scripts/feature.py entry point.                                 
                                                                    
  Two points from earlier are worth settling too:                   
  - D-10: commit the bootstrap work on agents as three reviewed     
    commits.                                                        
  - D-11: merge agents into dev through a human-reviewed bootstrap  
    PR before Phase 5; otherwise feature branches cut from dev won't
    have the docs or the contract tooling.                          
                                                                    
  When you're ready, say Proceed to Phase 2.  