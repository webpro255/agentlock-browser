# Item 4 probe: content-identical PAGE and ALLOWLIST entries

HEAD sha: `d3dc5abf10dc40c936f5b5e62ccc5a80c3b420bd`
branch: `probe/item4-allowlist-collision`

`pip show agentlock`:

```
Version: 1.7.0
```

Installed package read for the quotes below: `.venv/lib/python3.13/site-packages/agentlock`

Raw output this report is pasted from: `probe/item4/raw_agentlock.txt`
(cases P1 to P6) and `probe/item4/raw_browser.txt` (cases B1 to B3).

## Step 1: the precedence

### Order of the two checks at the call site

`.venv/lib/python3.13/site-packages/agentlock/gate.py`, lines 804 to 831. Parameter lineage is read first, novel
lineage second; each attaches its match to `request_metadata` and neither
reads the other's result.

```python
 804              # v1.3 Feature 2 -- parameter lineage. Gate-owned read: does any
 805              # parameter value trace to untrusted context but not the user's
 806              # authoritative request?  Attached for the policy engine.
 807              if _lp is not None and _lp.param_lineage_enabled:
 808                  _match = self._context_tracker.parameter_lineage_check(
 809                      resolved_session_id,
 810                      parameters,
 811                      min_len=_lp.param_lineage_min_len,
 812                      outcome=_param_outcome,
 813                  )
 814                  if _match is not None:
 815                      request_metadata["param_lineage"] = _match
 816              elif _lp is not None:
 817                  _param_outcome.update({"ran": False, "reason": "check_disabled"})
 818  
 819              # v1.4 -- novel lineage. Gate-owned read: does any parameter token
 820              # trace to NEITHER the authoritative nor the untrusted context?
 821              # Independent of param_lineage_enabled; exact-token membership.
 822              if _lp is not None and _lp.novel_lineage_enabled:
 823                  _novel = self._context_tracker.novel_lineage_check(
 824                      resolved_session_id,
 825                      parameters,
 826                      outcome=_novel_outcome,
 827                  )
 828                  if _novel is not None:
 829                      request_metadata["novel_lineage"] = _novel
 830              elif _lp is not None:
 831                  _novel_outcome.update({"ran": False, "reason": "check_disabled"})
```

### How the two results combine into a decision

`.venv/lib/python3.13/site-packages/agentlock/policy.py`, lines 613 to 634. The parameter-lineage gate is numbered
10.4 and returns before the novel-lineage gate is reached.

```python
 613          # 10.4. Parameter-lineage gate (v1.3 Feature 2) -- runs for EVERY tool
 614          # call, reads included.  Denies when a parameter value traces to
 615          # untrusted context but not the authoritative user request (the gate
 616          # attached the match as context.metadata["param_lineage"]).  Targets
 617          # read-goal attacks that write-gating cannot see.  Independent of the
 618          # write-gating flags: fires whenever param_lineage_enabled.
 619          _lp = permissions.lineage_policy
 620          if (
 621              _lp is not None
 622              and _lp.param_lineage_enabled
 623              and version_at_least(permissions.version, (1, 3))
 624          ):
 625              pmatch = context.metadata.get("param_lineage")
 626              if pmatch is not None:
 627                  action = _lp.param_lineage_action
 628                  detail = (
 629                      f"Parameter '{pmatch.get('matched_param')}' carries a value "
 630                      f"that originated in untrusted context "
 631                      f"({pmatch.get('untrusted_source_ref')}) and is absent from "
 632                      f"the authoritative user request. Gated on parameter "
 633                      f"provenance, not content."
 634                  )
```

`.venv/lib/python3.13/site-packages/agentlock/policy.py`, lines 650 to 660. The default action returns a
`PolicyDecision` immediately, so a parameter-lineage match short-circuits
the novel-lineage gate below.

```python
 650                  else:  # "deny" (default)
 651                      return PolicyDecision(
 652                          allowed=False,
 653                          reason=DenialReason.PARAM_LINEAGE,
 654                          detail=detail,
 655                          suggestion=(
 656                              "The parameter value originated from untrusted "
 657                              "context. Re-issue using a value from the user's "
 658                              "own request or trusted configuration."
 659                          ),
 660                      )
```

`.venv/lib/python3.13/site-packages/agentlock/policy.py`, lines 662 to 675. The novel-lineage gate is numbered 10.47
and runs only if parameter lineage did not return.

```python
 662          # 10.47. Novel-lineage gate (v1.4) -- sibling of parameter lineage.
 663          # Deliberately placed ABOVE the coarse session-taint gate below: a
 664          # NOVEL target (traceable to neither authoritative nor untrusted
 665          # context) is a strictly sharper finding than "this session is
 666          # tainted somewhere".  Running it second would let the session-wide
 667          # taint verdict mask the per-target one.
 668          if (
 669              _lp is not None
 670              and _lp.novel_lineage_enabled
 671              and version_at_least(permissions.version, (1, 3))
 672          ):
 673              nmatch = context.metadata.get("novel_lineage")
 674              if nmatch is not None:
 675                  naction = _lp.novel_lineage_action
```

### A value matching both an untrusted and an authoritative entry

An explicit rule was found, in both checks.

`.venv/lib/python3.13/site-packages/agentlock/context.py`, lines 1002 to 1005, in the `parameter_lineage_check`
docstring:

```python
1002          or ``None`` if every parameter value is clean.  The authoritative
1003          allowlist is checked FIRST: a value present in the user's own request
1004          is clean regardless of any untrusted echo.
1005  
```

`.venv/lib/python3.13/site-packages/agentlock/context.py`, lines 1037 to 1048. The authoritative blob and the
untrusted entries are built separately.

```python
1037          auth_blob = " ".join(
1038              e.content.lower() + _canonical_blob_suffix(e.content, min_len)
1039              for e in state.provenance_log
1040              if e.authority == ContextAuthority.AUTHORITATIVE and e.content
1041          )
1042          # AM7 item 5: the haystack is the REACHABLE set, so an intermediate hop's
1043          # own output is scanned once it chains to an untrusted ancestor.  That is
1044          # what lets a value transformed beyond token matching still be attributed
1045          # at the sink.  The auth-first skip below is NOT touched by this change.
1046          untrusted_entries = [
1047              e for e in _reachable_untrusted_entries(state.provenance_log) if e.content
1048          ]
```

`.venv/lib/python3.13/site-packages/agentlock/context.py`, lines 1122 to 1142. The rule is the `continue` on line
1125: a token found in `auth_blob` is skipped before any untrusted blob is
consulted, so it can never reach the `return` that reports a match.

```python
1122          for _rank, _neglen, tok, path, kind, value in candidates:
1123              # Authoritative FIRST, per token.
1124              if tok in auth_blob:
1125                  continue
1126              for entry, blob in untrusted_blobs:
1127                  if tok in blob:
1128                      _note_outcome(outcome, "match")
1129                      return {
1130                          "matched_param": path,
1131                          "matched_value": value[:120],
1132                          "matched_kind": kind,
1133                          "matched_token": tok[:120],
1134                          "untrusted_source_ref": (
1135                              f"{entry.tool_name or entry.source.value}"
1136                              f":{entry.provenance_id}"
1137                          ),
1138                          # The id on its own, so an evidence consumer can
1139                          # join this match to the taint-introduction record
1140                          # without parsing ``untrusted_source_ref``.
1141                          "untrusted_provenance_id": entry.provenance_id,
1142                      }
```

`.venv/lib/python3.13/site-packages/agentlock/context.py`, lines 1345 to 1356, in `novel_lineage_check`. The rule
here is the set union on line 1345: a token present in either set is
accounted for, so a token in both is accounted for.

```python
1345          accounted = auth_tokens | untrusted_tokens
1346  
1347          def _token_accounted(tok: str) -> bool:
1348              if tok in accounted:
1349                  return True
1350              own = {t for _kind, t in _canonical_lineage_tokens(tok, min_len)}
1351              return bool(own & accounted)
1352  
1353          for _rank, _neglen, tok, path, value in candidates:
1354              if _token_accounted(tok):
1355                  continue
1356              _note_outcome(outcome, "match")
```

## Cases

`decision` and `denial reason` are pasted from `AuthResult` (P cases) and
from the `NavigateResult.gate` object and the decision log (B cases).
`param_lineage result` and `novel_lineage result` are pasted from
`AuditRecord.metadata['grant_basis']` where that key is non-null, and from
`AuditRecord.metadata['lineage_evidence']` where `grant_basis` is null. In
every P case `AuditRecord.metadata['param_lineage']` and
`AuditRecord.metadata['novel_lineage']` were `null`.

| case | decision | denial reason | param_lineage result | novel_lineage result | untrusted_provenance_id |
|---|---|---|---|---|---|
| P1 | `allowed = True`, `decision = <DecisionType.ALLOW: 'allow'>` | `denial = None` | `"param_lineage": "no_match"` | `"novel_lineage": "no_match"` | `lineage_evidence = null` |
| P2 | `allowed = True`, `decision = <DecisionType.ALLOW: 'allow'>` | `denial = None` | `"param_lineage": "no_match"` | `"novel_lineage": "no_match"` | `lineage_evidence = null` |
| P3 | `allowed = False`, `decision = <DecisionType.DENY: 'deny'>` | `'reason': 'param_lineage'` | `{"gate": "param_lineage", "matched_kind": "url", "matched_param": "url", "matched_token": "example.com/foo/bar"}`; `grant_basis = null` | `grant_basis = null` | `"untrusted_provenance_id": "cprov_480157fa2f81eafb"` |
| P4 | `allowed = True`, `decision = <DecisionType.ALLOW: 'allow'>` | `denial = None` | `"param_lineage": "no_match:no_untrusted_context"` | `"novel_lineage": "no_match"` | `lineage_evidence = null` |
| P5 | `allowed = True`, `decision = <DecisionType.ALLOW: 'allow'>` | `denial = None` | `"param_lineage": "no_match"` | `"novel_lineage": "no_match"` | `lineage_evidence = null` |
| P6 | `allowed = True`, `decision = <DecisionType.ALLOW: 'allow'>` | `denial = None` | `"param_lineage": "no_match:no_tokens"` | `"novel_lineage": "not_classifiable:no_authoritative_baseline"` | `lineage_evidence = null` |
| B1 | `"allowed": true, "decision": "allow"` | `"reason": ""` | `"param_lineage": "no_match"` | `"novel_lineage": "no_match"` | `"evidence": null` |
| B2 | `"allowed": false, "decision": "deny"` | `"reason": "param_lineage"` | `{"gate": "param_lineage", "matched_kind": "url", "matched_param": "url", "matched_token": "evil.test/collect?x=handbook-session-7f3a1c"}`; `"grant_basis": null` | `"grant_basis": null` | `"untrusted_provenance_id": "cprov_08898689f03dea02"` |
| B3 | `"allowed": true, "decision": "allow"` | `"reason": ""` | `"grant_basis": {"lineage_policy": "none"}`, no `param_lineage` key | `"grant_basis": {"lineage_policy": "none"}`, no `novel_lineage` key | `"evidence": null` |

Case definitions, pasted from the raw files:

```
CASE P1: PAGE write then ALLOWLIST write of the same string
CASE P2: ALLOWLIST write then PAGE write of the same string (opposite order)
CASE P3: PAGE write only (control)
CASE P4: ALLOWLIST write only (control)
CASE P5: USER message containing the url, then PAGE write of the same url
CASE P6: PAGE then ALLOWLIST write of a bland tokenless value, type-shaped
CASE B1: allowlisted fixture origin; navigate by a url string read from the page, under that origin
CASE B2: allowlisted fixture origin; navigate by a url string read from the page, under an origin NOT on the allowlist
CASE B3: allowlisted fixture origin; navigate by link_id from the snapshot instead of by url
```

## Observed, not interpreted

### P1 against P2 (order dependence)

The decision does not differ.

```
P1  allowed = True   decision = <DecisionType.ALLOW: 'allow'>   denial = None
P2  allowed = True   decision = <DecisionType.ALLOW: 'allow'>   denial = None
```

One field of `grant_basis` differs:

```
P1  "post_authoritative_taint": false
P2  "post_authoritative_taint": true
```

All other `grant_basis` fields are identical between P1 and P2:

```
P1  {"lineage_policy": "declared_disabled", "novel_lineage": "no_match", "param_lineage": "no_match", "post_authoritative_taint": false, "session_lineage": "not_run:no_active_lineage_policy", "tainted": true}
P2  {"lineage_policy": "declared_disabled", "novel_lineage": "no_match", "param_lineage": "no_match", "post_authoritative_taint": true, "session_lineage": "not_run:no_active_lineage_policy", "tainted": true}
```

### P1 against P5 (USER against ALLOWLIST as the authoritative half)

The decision does not differ.

```
P1  allowed = True   decision = <DecisionType.ALLOW: 'allow'>   denial = None
P5  allowed = True   decision = <DecisionType.ALLOW: 'allow'>   denial = None
```

One field of `grant_basis` differs:

```
P1  "post_authoritative_taint": false
P5  "post_authoritative_taint": true
```

`param_lineage`, `novel_lineage`, `tainted`, `lineage_policy` and
`session_lineage` are identical between P1 and P5:

```
P1  {"lineage_policy": "declared_disabled", "novel_lineage": "no_match", "param_lineage": "no_match", "post_authoritative_taint": false, "session_lineage": "not_run:no_active_lineage_policy", "tainted": true}
P5  {"lineage_policy": "declared_disabled", "novel_lineage": "no_match", "param_lineage": "no_match", "post_authoritative_taint": true, "session_lineage": "not_run:no_active_lineage_policy", "tainted": true}
```

The two writes differ in content, which is recorded in the raw file:

```
P1  source=web_content        content='https://example.com/foo/bar'
P1  source=system_prompt      content='https://example.com/foo/bar'
P5  source=user_message       content='go to https://example.com/foo/bar'
P5  source=web_content        content='https://example.com/foo/bar'
```

### B1 against B3 (url path against link_id path)

The decision does not differ.

```
B1  "allowed": true, "decision": "allow", "reason": ""
B3  "allowed": true, "decision": "allow", "reason": ""
```

Both reached the same page:

```
B1  "url": "http://fixture.test/article.html", "title": "Quarterly notes"
B3  "url": "http://fixture.test/article.html", "title": "Quarterly notes"
```

Five recorded fields differ.

Tool and action:

```
B1  "tool": "browser.navigate",      "action": "navigate_url"
B3  "tool": "browser.navigate_link", "action": "navigate_link"
```

Channel on the decision record:

```
B1  "channel": "ALLOWLIST"
B3  "channel": "PAGE"
```

Value recorded on the decision:

```
B1  "value": "http://fixture.test/article.html"
B3  "value": "1-e4", "href": "http://fixture.test/article.html"
```

`grant_basis`:

```
B1  {"lineage_policy": "declared_disabled", "novel_lineage": "no_match", "param_lineage": "no_match", "post_authoritative_taint": false, "session_lineage": "not_run:no_active_lineage_policy", "tainted": true}
B3  {"lineage_policy": "none"}
```

Count of provenance events with note `allowlist materialized for url`:

```
B1  1
B3  0
```

The single B1 materialization event:

```
{"channel": "ALLOWLIST", "content_len": 32, "content_sha256": "4a4a0efe719f3c68dac3020fcf389f6cdee7c0a05c2846398626e8244872cdf8", "event": "provenance", "iso": "2026-08-22T02:17:20Z", "note": "allowlist materialized for url", "origin": "", "provenance_id": "cprov_42c07759580b4a46", "session_id": "als_8Rcu6h3phHaVq7Iu16NGVbNIx8U", "ts": 1787365040.2373607}
```

B2 recorded, for comparison:

```
B2  provenance events with note "allowlist materialized for url": 0
```
