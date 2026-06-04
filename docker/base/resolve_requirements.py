import argparse
import tomllib
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pyproject_path", type=Path)
    parser.add_argument("extras", nargs="?", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.pyproject_path.open("rb") as file_handle:
        project = tomllib.load(file_handle)["project"]

    base_dependencies = project["dependencies"]
    optional_dependencies = project["optional-dependencies"]
    selected_extras = tuple(
        extra.strip() for extra in args.extras.split(",") if extra.strip()
    )
    resolved_dependencies: list[str] = []

    def add_dependency(spec: str) -> None:
        if spec not in resolved_dependencies:
            resolved_dependencies.append(spec)

    def resolve_extra(extra_name: str) -> None:
        for spec in optional_dependencies.get(extra_name, []):
            if spec.startswith("banking-agent[") and spec.endswith("]"):
                nested_extras = spec.removeprefix("banking-agent[").removesuffix("]")
                for nested_extra in (
                    part.strip() for part in nested_extras.split(",") if part.strip()
                ):
                    resolve_extra(nested_extra)
                continue
            add_dependency(spec)

    for dependency in base_dependencies:
        add_dependency(dependency)

    for extra in selected_extras:
        resolve_extra(extra)

    print("\n".join(resolved_dependencies))


if __name__ == "__main__":
    main()
