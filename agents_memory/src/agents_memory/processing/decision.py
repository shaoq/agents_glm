from agents_memory.models import (
    Action,
    ActionPlan,
    CandidateMemory,
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

        if kinds == {RelationKind.CONTRADICT} and candidate.type is MemoryType.EVENT:
            return ActionPlan(
                candidate_index=candidate_index,
                candidate=candidate,
                action=Action.ADD,
                matches=tuple(active),
                reason="new event does not replace an earlier event",
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
