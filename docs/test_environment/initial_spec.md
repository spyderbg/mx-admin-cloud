Да — това вече е много по-смислена архитектура. Аз бих я направил като **затворен self-healing loop**, в който локалният модел е reviewer/diagnostician, а Codex е executor.

```text
        ┌───────────────────────────────┐
        │ 1. Scan project / run checks │
        │ tests, lint, logs, static    │
        │ analysis, TODOs, warnings    │
        └──────────────┬────────────────┘
                       │
                       ▼
        ┌───────────────────────────────┐
        │ 2. LOCAL MODEL               │
        │ Detect / classify problems   │
        │                              │
        │ → is this really a bug?      │
        │ → severity                   │
        │ → affected files             │
        │ → expected behavior          │
        │ → create structured task     │
        └──────────────┬────────────────┘
                       │
                       ▼
               tasks.sqlite
                       │
                       ▼
        ┌───────────────────────────────┐
        │ 3. CODEX                     │
        │ Fix one task                 │
        │                              │
        │ → inspect                    │
        │ → modify                     │
        │ → test                       │
        │ → lint                       │
        │ → commit                     │
        └──────────────┬────────────────┘
                       │
                       ▼
        ┌───────────────────────────────┐
        │ 4. LOCAL MODEL               │
        │ Independent review           │
        │                              │
        │ → inspect diff               │
        │ → inspect test results       │
        │ → verify original issue      │
        │ → search for regressions     │
        └──────────────┬────────────────┘
                       │
                 ┌─────┴─────┐
                 │           │
              PASS          FAIL
                 │           │
                 ▼           ▼
              DONE      feedback → Codex
```

Това според мен е значително по-добре от просто „пускам Codex да търси проблеми“, защото разделяш **откриването**, **поправката** и **оценката** между различни роли.

### 1. Local model като Problem Detector

Първата му задача не трябва да бъде да променя код. Само анализ.

Например worker-ът първо може да изпълни:

```bash
pytest
pylint src
python -m compileall src
npm test
npm run lint
npm run build
```

и да подаде на локалния модел:

```text
Repository snapshot
Git diff: clean

Test output:
...

Lint output:
...

Build output:
...

Recent application logs:
...

Analyze the repository for actionable problems.

Do NOT modify code.

For every confirmed problem return:
- id
- title
- type
- severity
- evidence
- affected files
- reproduction steps
- expected behavior
- acceptance criteria

Do not create speculative tasks.
```

Тук бих използвал **structured JSON output**, вместо свободен текст. LM Studio поддържа JSON Schema structured outputs. ([LM Studio][1])

Например:

```json
{
  "issues": [
    {
      "title": "Translation endpoint returns 500 when no providers are active",
      "type": "bug",
      "severity": "high",
      "confidence": 0.94,
      "files": [
        "src/services/translation/service.py"
      ],
      "evidence": [
        "pytest tests/test_translation.py::test_no_active_provider failed"
      ],
      "acceptance_criteria": [
        "endpoint returns HTTP 503",
        "existing translation tests continue to pass",
        "new regression test is added"
      ]
    }
  ]
}
```

Това е важно, защото иначе локалният модел лесно ще започне да ти генерира „подобрения“, които всъщност не са проблеми.

### 2. SQLite task queue

Аз бих използвал нещо подобно:

```sql
tasks
-----
id
title
description
severity
confidence

status
created_at
started_at
finished_at

branch
worktree

detector_report
codex_report
review_report

attempt
max_attempts
```

Status примерно:

```text
DETECTED
↓
CONFIRMED
↓
ASSIGNED
↓
FIXING
↓
TESTING
↓
REVIEW
↓
DONE
```

и при проблем:

```text
REVIEW_FAILED
      ↓
FIXING
```

Това ти позволява процесът да бъде рестартиран без да загубиш state.

### 3. Codex трябва да получава много конкретен task

Вместо:

```text
Fix the project.
```

подаваш:

```text
Fix task BUG-0027.

Problem:
Translation endpoint returns HTTP 500 when no
translation providers are active.

Evidence:
...

Affected files:
...

Acceptance criteria:
1. return 503
2. add regression test
3. all existing tests must pass
4. pylint must pass

Constraints:
- Do not modify unrelated code.
- Do not change public API unless necessary.
- Do not disable tests.
- Do not weaken lint rules.
```

След това:

```bash
codex exec ...
```

Точно `codex exec` е интересен за твоя случай, защото има machine-readable execution режим. Текущият CLI поддържа JSONL event output чрез `--json` и structured финален резултат чрез `--output-schema`. ([GitHub][2])

Например Codex може да върне:

```json
{
    "status": "fixed",
    "files_changed": [
        "src/services/translation/service.py",
        "tests/test_translation.py"
    ],
    "tests_added": 1,
    "tests_passed": 147,
    "tests_failed": 0,
    "summary": "..."
}
```

### 4. Най-важната част: independent verification

Тук **не бих казал на local model просто:**

```text
Codex каза, че проблемът е оправен. Провери.
```

Това го bias-ва.

Вместо това му даваш:

```text
ORIGINAL ISSUE
...

ORIGINAL ACCEPTANCE CRITERIA
...

ORIGINAL CODE
...

PATCH
git diff ...
    
TEST RESULTS
...

LINT RESULTS
...

BUILD RESULTS
...
```

и питаш:

```text
Act as an independent reviewer.

Determine whether the original issue is completely resolved.

Check for:
- incomplete fix
- incorrect assumptions
- missing edge cases
- regressions
- tests that don't really test the bug
- weakened validations
- unrelated modifications

Return exactly one verdict:

PASS
FAIL
NEEDS_MORE_EVIDENCE
```

Например:

```json
{
    "verdict": "FAIL",
    "confidence": 0.91,
    "remaining_problems": [
        {
            "description": "Empty provider list is fixed, but disabled providers still cause the same exception.",
            "file": "src/services/translation/service.py",
            "line": 83
        }
    ],
    "required_actions": [
        "Handle the disabled-provider case",
        "Add regression test"
    ]
}
```

И това отново влиза в Codex.

```text
Local review
     │
     ▼
   FAIL
     │
     ▼
Codex iteration #2
     │
     ▼
test
     │
     ▼
review
```

Това може да се повтори примерно максимум:

```python
MAX_FIX_ATTEMPTS = 3
```

След което:

```text
NEEDS_HUMAN_REVIEW
```

Така предотвратяваш infinite loop.

### 5. Още по-добре: local model да не е единствената проверка

Бих направил verification-а на три слоя:

```text
                  VERIFY
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
 Deterministic    Local AI    Regression
    checks         review       search
        │           │           │
 pytest/build     diff/code    additional
 lint/typecheck     review       tests
        │           │           │
        └───────────┼───────────┘
                    ▼
                 decision
```

Тоест AI никога не трябва да има последната дума върху нещо, което може да бъде проверено детерминистично.

Например:

```python
if pytest_failed:
    verdict = "FAIL"

elif build_failed:
    verdict = "FAIL"

elif lint_failed:
    verdict = "FAIL"

elif local_reviewer == "FAIL":
    verdict = "FAIL"

else:
    verdict = "PASS"
```

Това е много по-надеждно.

### 6. Git worktree за всеки task

Това според мен ще пасне много добре на твоя workflow.

Например:

```text
repo/
worktrees/
   BUG-001/
   BUG-002/
   BUG-003/
```

Worker:

```bash
git worktree add \
    ../worktrees/BUG-002 \
    -b ai/BUG-002 \
    dev
```

Codex работи само там.

След успешен review:

```text
ai/BUG-002
     ↓
commit
     ↓
review
     ↓
merge candidate
```

Така можеш дори да имаш:

```text
Detector
   │
   ├── BUG-101 → Codex worker #1
   ├── BUG-102 → Codex worker #2
   └── BUG-103 → Codex worker #3
```

без агентите да си пречат.

### 7. Бих добавил и Task Critic преди Codex

Има една важна стъпка между detection и fixing:

```text
Detector
   ↓
Task candidate
   ↓
Critic
   ↓
real issue?
```

Например:

```text
confidence < 0.75
       ↓
discard / human review
```

или:

```text
Detector: "potential bug"
      ↓
Critic model
      ↓
"Cannot reproduce"
      ↓
NO TASK
```

Това ще ти спести много Codex tokens.

### 8. Local model може да използва LM Studio

Архитектурата може да изглежда така:

```text
Python AI Worker
        │
        ├─────────────► LM Studio
        │                localhost:1234
        │
        │                Local model
        │                Problem detection
        │                Review
        │
        │
        └─────────────► codex exec
                            │
                            ▼
                          repo
```

LM Studio има OpenAI-compatible `/v1/responses`, stateful responses и custom tools, така че Python orchestrator-а може лесно да работи с него през стандартния OpenAI client. ([LM Studio][3])

Например:

```python
from openai import OpenAI

local_ai = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="local"
)

response = local_ai.responses.create(
    model="qwen-local",
    input=prompt,
)
```

### Архитектурата, която бих използвал

За твоя случай:

```text
ai-worker/
│
├── worker.py
│
├── detector/
│   ├── scanner.py
│   ├── prompts.py
│   └── schemas.py
│
├── fixer/
│   ├── codex.py
│   └── worktree.py
│
├── reviewer/
│   ├── reviewer.py
│   └── prompts.py
│
├── checks/
│   ├── pytest.py
│   ├── pylint.py
│   ├── npm.py
│   └── build.py
│
├── tasks/
│   ├── repository.py
│   └── models.py
│
└── data/
    └── tasks.db
```

И основният loop да е приблизително:

```python
while True:

    findings = detector.scan_repository()

    tasks = critic.confirm(findings)

    for task in tasks:

        worktree = create_worktree(task)

        for attempt in range(3):

            codex.fix(
                task=task,
                worktree=worktree
            )

            checks = run_all_checks(worktree)

            if not checks.success:
                continue

            review = local_model.review(
                issue=task,
                diff=get_diff(worktree),
                checks=checks
            )

            if review.verdict == "PASS":
                task.status = "DONE"
                break

            task.add_feedback(review)
```

Аз бих добавил още едно важно правило: **local detector-ът да няма write access до repository-то**, а reviewer-ът също да е read-only. Само Codex получава право да променя файловете. Това прави системата много по-предвидима.

Така ролите стават:

**Local model #1 = Detective**
„Какъв е проблемът?“

**Codex = Engineer**
„Оправи конкретния проблем.“

**Local model #2 = Reviewer**
„Наистина ли е оправен напълно?“

**pytest/build/lint = Judge**
„Кодът реално работи ли?“

Това вече може съвсем реалистично да работи **цяла нощ автономно**, като намира проблем → създава task → прави worktree → Codex го оправя → локалният модел review-ва → връща го за поправка при нужда → преминава към следващия task. За твоя Python/Vue/C++ workflow бих го направил като отделен Python daemon, а не като shell script, защото много бързо ще ти потрябва persistence, retries, priorities, logs и concurrency.

[1]: https://lmstudio.ai/docs/developer/openai-compat/structured-output?utm_source=chatgpt.com "Structured Output | LM Studio"
[2]: https://github.com/openai/codex/blob/main/codex-rs/exec/src/cli.rs?utm_source=chatgpt.com "codex/codex-rs/exec/src/cli.rs at main · openai/codex · GitHub"
[3]: https://lmstudio.ai/docs/developer/openai-compat?utm_source=chatgpt.com "OpenAI Compatibility Endpoints | LM Studio"
