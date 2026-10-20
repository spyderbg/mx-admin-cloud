                  Feature Coordinator
                          |
                          v
                 Create GitHub Issues
                          |
                          v
                 Create feature branch
                          |
            +-------------+-------------+
            |             |             |
            v             |             |
     Backend Agent        |             |
      Claude Code         |             |
            |             |             |
       Implement API      |             |
            |             |             |
       Verify Backend     |             |
            |             |             |
            +-------------+-------------+
                          |
                   API available
                          |
                +---------+---------+
                |                   |
                v                   v
           CLI Agent          Frontend Agent
           Claude Code            Codex
                |                   |
                +---------+---------+
                          |
                          v
                  Integration Gates
                          |
                          v
                 One Pull Request
                          |
                          v
                         dev