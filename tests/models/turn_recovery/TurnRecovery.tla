------------------------ MODULE TurnRecovery ------------------------
EXTENDS Naturals, FiniteSets, TLC
CONSTANT GuardUnknown
VARIABLES phase, owner, live, attempts, effects, settlements, failures, authorized
vars == <<phase, owner, live, attempts, effects, settlements, failures, authorized>>
Init == /\ phase = "new" /\ owner = 0 /\ live = {}
        /\ attempts = 0 /\ effects = 0 /\ settlements = 0
        /\ failures = 0 /\ authorized = TRUE
Admit(e) == /\ phase = "new" /\ owner = 0 /\ authorized
            /\ owner' = e /\ live' = {e}
            /\ UNCHANGED <<phase, attempts, effects, settlements, failures, authorized>>
Dispatch == /\ phase = "new" /\ owner # 0 /\ authorized
            /\ phase' = "intent" /\ attempts' = attempts + 1
            /\ UNCHANGED <<owner, live, effects, settlements, failures, authorized>>
Tool == /\ phase = "intent" /\ owner # 0 /\ authorized /\ effects < attempts
        /\ effects' = effects + 1
        /\ UNCHANGED <<phase, owner, live, attempts, settlements, failures, authorized>>
SaveResult == /\ phase = "intent" /\ owner # 0 /\ authorized /\ effects = attempts
              /\ phase' = "result"
              /\ UNCHANGED <<owner, live, attempts, effects, settlements, failures, authorized>>
Crash == /\ owner # 0 /\ phase \in {"new", "intent", "result"} /\ failures < 2
         /\ owner' = 0 /\ failures' = failures + 1
         /\ UNCHANGED <<phase, live, attempts, effects, settlements, authorized>>
Recover == /\ owner = 0 /\ phase \in {"intent", "result"}
           /\ phase' = IF phase = "result" THEN "result"
                      ELSE IF GuardUnknown THEN "unknown" ELSE "new"
           /\ live' = {} /\ owner' = IF phase = "result" THEN 1 ELSE 0
           /\ UNCHANGED <<attempts, effects, settlements, failures, authorized>>
Settle == /\ phase = "result" /\ owner # 0 /\ authorized
          /\ phase' = "settled" /\ settlements' = 1 /\ owner' = 0 /\ live' = {}
          /\ UNCHANGED <<attempts, effects, failures, authorized>>
Revoke == /\ authorized /\ phase \notin {"settled", "unknown", "revoked"}
          /\ authorized' = FALSE /\ phase' = "revoked" /\ owner' = 0 /\ live' = {}
          /\ UNCHANGED <<attempts, effects, settlements, failures>>
Advance == (\E e \in {1,2}: Admit(e)) \/ Dispatch \/ Tool \/ SaveResult \/ Settle
Next == Advance \/ Crash \/ Recover \/ Revoke
Spec == Init /\ [][Next]_vars /\ WF_vars(Advance) /\ WF_vars(Recover)
OneAuthority == owner \in {0,1,2}
NoReplacement == attempts <= 1
NoDuplicateEffect == effects <= 1
AtMostOneSettlement == settlements <= 1
NoUnauthorizedAcceptance == ~authorized => settlements = 0
Terminates == <> (phase \in {"settled", "unknown", "revoked"})
=============================================================================
