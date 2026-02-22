from collections import defaultdict
from collections.abc import Mapping


def build_dependency_waves(task_ids: list[str], depends_on_by_task: Mapping[str, list[str]]) -> list[list[str]]:
    """Build topological waves from task dependencies.

    Each wave contains tasks whose dependencies were completed in earlier waves.
    Falls back to a single remaining wave if a cycle is detected.
    """
    ordered_ids = [tid for tid in task_ids if tid]
    known = set(ordered_ids)
    deps: dict[str, list[str]] = {
        tid: [dep for dep in depends_on_by_task.get(tid, []) if dep in known]
        for tid in ordered_ids
    }

    in_degree: dict[str, int] = {tid: len(dep_list) for tid, dep_list in deps.items()}
    graph: dict[str, list[str]] = defaultdict(list)
    for tid, dep_list in deps.items():
        for dep in dep_list:
            graph[dep].append(tid)

    waves: list[list[str]] = []
    remaining = set(ordered_ids)
    while remaining:
        ready = [tid for tid in ordered_ids if tid in remaining and in_degree.get(tid, 0) == 0]
        if not ready:
            waves.append([tid for tid in ordered_ids if tid in remaining])
            break

        waves.append(ready)
        for tid in ready:
            remaining.discard(tid)
            for child in graph.get(tid, []):
                if child in in_degree:
                    in_degree[child] = max(0, in_degree[child] - 1)

    return [wave for wave in waves if wave]
