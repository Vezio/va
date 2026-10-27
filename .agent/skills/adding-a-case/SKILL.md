---
name: adding-a-case
description: Use when adding or changing a case, a target capability, a skip class or a command in a runkit app
---

# Adding a case

1. **Name what it proves.** `group.what_it_proves`; reuse an existing group unless you mean a
   new one. The id is the comparison key across runs: never rename it casually.
2. **Declare what it needs.** `@REGISTRY.case(id, needs={...}, variants=(...), tags={...})`. A target that
   does not declare a capability gets `skip:undeclared`; never test for the capability inside
   the case and pass.
3. **Record, then decide.** `ctx.require(name, observed, expected)` for the deciding check,
   `ctx.check(...)` for supporting observations, `ctx.fail(...)` when you compute the verdict
   yourself. A bare `assert` works but records nothing.
4. **Stay target-agnostic.** Use what the target declares (paths, limits); never branch on its
   name.
5. **No retries, no sleeps-until-green.** A flaky target is a finding.
   If the case measures something another request would disturb (a cache, a latency, a rate
   limit), mark it `exclusive=True`, so it runs alone even with `--workers`. Anything it
   changes on `ctx.state` must be thread-safe. Give a case that can hang its own `timeout_s`.
6. **Prove it fails.** Add the defect to `tests/fake_server.py` (`Defects`) and a parametrised
   row in the test that asserts `fail` with a not-ok check.
7. **Skip honestly.** `ctx.skip(class, reason)` with a class from the app's `SkipClasses`; when
   none fits, extend them in `App(skip_classes=DEFAULT_SKIP_CLASSES.extend(SkipClass(...)))`.
   Never reuse one for another reason. An unknown class is recorded as an error.

| Thought | Reality |
|---|---|
| "It passed, so it works" | A case that never failed was never tested |
| "Retry once, the target is flaky" | The retry hides the failure the case exists to catch |
| "Status 200 is enough" | Check the body and fields that make the answer correct |
| "Skip it as not_implemented, it is easier" | If the target lacks it, it is `undeclared` |
