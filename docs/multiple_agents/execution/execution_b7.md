I've fixed B-7 with a test-only change. The full backend suite (919 tests) now passes with no writes outside pytest's temporary directories, and the real content/ was never touched. Nothing is committed; I'm stopping for your approval.

1. Root cause. In test_delete_item_image_last_remaining_is_allowed, the last assertion called provider.item_images() after the with patch_fs_settings() block had ended. That call used the real settings, so it resolved the real content/ and its registry lock created content/.categories_registry.lock and content/foods/.items_registry.lock. Three things hid it:
- the lock code silently falls back to an in-process lock on a read-only tree, so the test still passed there;
- .gitignore hides the lock files;
- the assertion was quietly checking real data instead of the test's own files.

It was also riskier than leftover lock files: the same lock code finishes interrupted registry writes, so in a bad case it could have rewritten real registry files.

2. Files changed (on fix/backend-test-isolation-b7, created from dev 37c8f70):
- backend/src/services/content/__tests__/test_content_filesystem_item_images.py (+2/−1)
- backend/src/conftest.py (new, 52 lines)

No runtime code, config, docs, content/, data/, frontend/, cli/, mxfix or mxissues changed.

 3. The fix:                                                       
  - The test: the item_images() call moved inside the patched block,
    so the test now checks its own temporary tree.                  
  - A safety net for every backend test (backend/src/conftest.py):  
    for each test it points the content, archive, output, temp and  
    user-database locations at a temporary path that doesn't exist. 
    CONFIG_DIR stays real, because tests read the tracked config.   
    - A test that sets its own paths works as before.               
    - A test that forgets now fails ("Content root … does not       
      exist") instead of reaching real data.                        
    - If a test writes there, it fails with the paths named, and the
      leftovers are cleaned up even when the test itself failed.    
                                                                    
  4. Other tests with the same problem: none. I checked the whole   
  suite on a disposable copy four ways:                             
                                                                    
  ┌────────────────────────────────┬────────────────────────────┐   
  │             Check              │           Result           │   
  ├────────────────────────────────┼────────────────────────────┤   
  │ Snapshot of the copy after     │ only the B-7 test changed  │   
  │ each test                      │ anything                   │   
  ├────────────────────────────────┼────────────────────────────┤   
  │ Trace of every file write      │ only B-7's two lock files  │   
  │ attempt, including failed ones │                            │   
  ├────────────────────────────────┼────────────────────────────┤   
  │ Suite with content/ and the    │ only B-7 failed; no test   │   
  │ tracked backend/data/users.db  │ reads the real tree or the │   
  │ made unreadable                │  database                  │   
  ├────────────────────────────────┼────────────────────────────┤   
  │                                │ 10 provider calls outside  │   
  │ Static scan of the test code   │ the patch; 9 are harmless  │   
  │                                │ (explicit temporary paths  │   
  │                                │ or pure functions)         │   
  └────────────────────────────────┴────────────────────────────┘   

  5–6. Tests run. Every backend run was on a disposable export      
  inside a sandbox where the host, including the real checkout, was 
  read-only and the network was off. My helper scripts are in       
  /tmp/claude-1000/-media-spyder--Projects-MarXman-mx-admin-cloud/56
  c8d877-68dc-4ebd-9f1b-58073ed115b5/scratchpad/b7/.                
                                                                    
  ┌───────────────────────────┬─────────────────────────────────┐   
  │            Run            │             Result              │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ dev as it is (baseline)   │ 919 passed; B-7 created its two │   
  │                           │  lock files in the copy         │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ The B-7 test alone, fixed │ 1 passed, no file changes       │   
  │  (run first)              │                                 │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Full suite, fixed         │ 919 passed, no file changes     │   
  │                           │ outside pytest's temp dirs      │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Same, with content/ and   │ 919 passed                      │   
  │ users.db unreadable       │                                 │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Write trace, fixed        │ no write attempt anywhere in    │   
  │                           │ the tree                        │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Check A: the original     │                                 │   
  │ test, with the new safety │ fails (the leak is now visible) │   
  │  net                      │                                 │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Check B: a deliberately   │ caught and named                │   
  │ leaking test              │ (content/leak.txt); the next    │   
  │                           │ test passes                     │   
  ├───────────────────────────┼─────────────────────────────────┤   
  │ Backend pylint src/       │ 10.00/10, no messages (Python   │   
  │                           │ 3.12.3)                         │   
  └───────────────────────────┴─────────────────────────────────┘   
                                                                    
  7. Real content/: untouched. Since the first test run of this     
  task, no file under content/, data/ or backend/data/ changed.     
  There are no lock files, and git status (including ignored files) 
  is empty for those paths.     

    8. Coordinator backend-test gate: validated without changing any
  committed config. I removed backend-tests from the blocked-gates
  list only in memory.
  
  ┌────────────────────────┬────────────────────────────────────┐
  │         Check          │               Result               │
  ├────────────────────────┼────────────────────────────────────┤
  │ Committed config       │ the gate is still refused          │
  │                        │ ("blocked", exit 125)              │
  ├────────────────────────┼────────────────────────────────────┤
  │ Gate against the real  │ passed, exit 0, 919 tests in 24 s; │
  │ checkout (read-only,   │  checkout unchanged                │
  │ no network)            │                                    │
  ├────────────────────────┼────────────────────────────────────┤
  │ Coordinator flow, task │ verified, with the gate result,    │
  │  with a passing test   │ commit and log saved as evidence   │
  ├────────────────────────┼────────────────────────────────────┤
  │ Same flow, task with a │ exit 1 recorded, task failed       │
  │  failing test          │ ("gates failed:                    │
  │                        │ ['backend-tests']"), not verified  │
  └────────────────────────┴────────────────────────────────────┘
                                                                    
  The flow ran on a disposable repo and state folder. Nothing       
  appeared outside the allowed paths, and the logs stayed in the    
  state folder.                                                     
                                                                    
  9. Still open:                                                    
  - Unblocking the gate in real use means removing backend-tests    
    from the blocked-gates list in                                  
    scripts/feature_coordinator/config.py and in                    
    docs_agents/coordination/project-config.example.yaml. The config
    check requires the two to match, and this task forbids doc      
    edits, so it needs your approval after this fix reaches dev. The
    B-7 notes in backend-task-b7.md and phase-status.md need        
    updating then too.                                              
  - Left alone on purpose (runtime code):                           
    - The silent read-only fallback in the lock code is production  
      behavior.                                                     
    - src/db/database.py resolves DB_PATH against the current       
      directory, while the other paths resolve against backend/. The
      new safety net covers it in tests.                            
  - Not run: the backend tests unsandboxed in the real checkout.    
    It's safe now, but all runs here were sandboxed by design.      
    Frontend and CLI tests weren't run because nothing there        
    changed.                                                        
                                                                                                     10. Git: branch fix/backend-test-isolation-b7 at 37c8f70. Changes:
  the modified test file and the new backend/src/conftest.py. An    
  untracked file I didn't create,                                   
  docs/multiple_agents/tests/first_test.md (20:55 today), appeared  
  during the session; I left it alone and it isn't part of the fix. 
  agents and dev are untouched, and nothing was pushed.             
                                                                    
  The commit I'd make contains only the two backend files: "Isolate 
  backend tests from the real data locations (B-7)". Shall I commit 
  it?        
                                                                    
                                                                    