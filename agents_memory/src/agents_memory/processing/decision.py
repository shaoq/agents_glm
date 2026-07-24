from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
    EventIdentity,
    MemoryRecord,
    MemoryType,
    RelationKind,
    RelationMatch,
    Validity,
)


class AmbiguousDecision(ValueError):
    pass


class DecisionEngine:
    def decide(
        self,
        candidate_index: int,
        candidate: CandidateMemory,
        histories: list[MemoryRecord],
        relations: list[RelationMatch],
    ) -> ActionPlan:
        by_id = {record.id: record for record in histories}
        active = [
            relation
            for relation in relations
            if relation.memory_id in by_id
            and by_id[relation.memory_id].validity is Validity.ACTIVE
            and relation.relation is not RelationKind.NONE
        ]
        if not active:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.ADD,
                reason="no active related memory",
            )

        kinds = {relation.relation for relation in active}
        if candidate.type is MemoryType.EVENT:
            return self._decide_event(candidate_index, candidate, active)

        change_kinds = {RelationKind.CONTRADICT, RelationKind.CORRECT}
        if kinds & change_kinds and kinds - change_kinds:
            raise AmbiguousDecision("mixed additive/duplicate and change relations")
        if RelationKind.CONTRADICT in kinds and RelationKind.CORRECT in kinds:
            raise AmbiguousDecision("mixed contradict and correct relations")

        if kinds == {RelationKind.DUPLICATE}:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.NOOP,
                target_ids=tuple(item.memory_id for item in active),
                matches=tuple(active),
                relation=RelationKind.DUPLICATE,
                reason="duplicates current active memory",
            )

        if kinds <= {RelationKind.SUPPLEMENT}:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.ADD,
                matches=tuple(active),
                reason="supplements existing memories",
            )

        if kinds <= change_kinds:
            relation = next(iter(kinds))
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.UPDATE,
                target_ids=tuple(item.memory_id for item in active),
                matches=tuple(active),
                relation=relation,
                reason="current fact changed" if relation is RelationKind.CONTRADICT else "correction",
            )

        raise AmbiguousDecision(f"unsupported relation combination: {sorted(kinds)}")

    @staticmethod
    def _decide_event(
        candidate_index: int,
        candidate: CandidateMemory,
        active: list[RelationMatch],
    ) -> ActionPlan:
        identities = {item.identity or EventIdentity.UNKNOWN for item in active}
        if EventIdentity.UNKNOWN in identities:
            unresolved = [
                item
                for item in active
                if (item.identity or EventIdentity.UNKNOWN) is EventIdentity.UNKNOWN
            ]
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.DEFER,
                target_ids=tuple(item.memory_id for item in active),
                matches=tuple(active),
                relation=next(
                    (
                        item.relation
                        for item in unresolved
                        if item.relation
                        in (RelationKind.CONTRADICT, RelationKind.CORRECT)
                    ),
                    unresolved[0].relation,
                ),
                reason="event identity requires more evidence",
            )
        if identities == {EventIdentity.DIFFERENT_EVENT}:
            if any(
                item.explicit_correction
                or item.relation is RelationKind.CORRECT
                for item in active
            ):
                return ActionPlan(
                    candidate_index=candidate_index,
                    candidate=candidate,
                    action=Action.DEFER,
                    target_ids=tuple(item.memory_id for item in active),
                    matches=tuple(active),
                    relation=RelationKind.CORRECT,
                    reason="explicit correction conflicts with different event identity",
                )
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.ADD,
                matches=tuple(active),
                reason="different event occurrence",
            )
        if identities != {EventIdentity.SAME_EVENT}:
            raise AmbiguousDecision("mixed same and different event identities")

        kinds = {item.relation for item in active}
        if kinds == {RelationKind.DUPLICATE}:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.NOOP,
                target_ids=tuple(item.memory_id for item in active),
                matches=tuple(active),
                relation=RelationKind.DUPLICATE,
                reason="duplicates the same event",
            )
        if kinds <= {RelationKind.SUPPLEMENT}:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.ADD,
                matches=tuple(active),
                reason="supplements the same event",
            )
        if kinds == {RelationKind.CORRECT} and all(
            item.explicit_correction for item in active
        ):
            relation = RelationKind.CORRECT
        elif kinds == {RelationKind.CONTRADICT}:
            relation = RelationKind.CONTRADICT
        else:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.DEFER,
                target_ids=tuple(item.memory_id for item in active),
                matches=tuple(active),
                relation=next(iter(kinds)),
                reason="same event relation requires more evidence",
            )
        return ActionPlan(
            candidate_index=candidate_index,
            candidate=candidate,
            action=Action.UPDATE,
            target_ids=tuple(item.memory_id for item in active),
            matches=tuple(active),
            relation=relation,
            reason=(
                "same event corrected"
                if relation is RelationKind.CORRECT
                else "same event state changed"
            ),
        )
