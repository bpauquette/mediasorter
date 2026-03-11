from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FilesystemMetadataRecord:
    frn: int
    parent_frn: int
    name: str
    is_dir: bool
    size: int = 0


@dataclass
class MetadataTreeNode:
    frn: int
    parent_frn: int
    name: str
    is_dir: bool
    size: int = 0
    children: list["MetadataTreeNode"] = field(default_factory=list)


def build_metadata_tree(records: list[FilesystemMetadataRecord], *, root_name: str, root_path: str) -> MetadataTreeNode:
    root = MetadataTreeNode(frn=0, parent_frn=0, name=root_name, is_dir=True, size=0, children=[])
    nodes: dict[int, MetadataTreeNode] = {0: root}

    for record in records:
        nodes[record.frn] = MetadataTreeNode(
            frn=int(record.frn),
            parent_frn=int(record.parent_frn),
            name=str(record.name or ""),
            is_dir=bool(record.is_dir),
            size=max(0, int(record.size or 0)),
            children=[],
        )

    for frn, node in list(nodes.items()):
        if frn == 0:
            continue
        parent = nodes.get(node.parent_frn, root)
        parent.children.append(node)

    _roll_up_sizes(root)
    _sort_children(root)
    root.name = root_name
    return root


def flatten_metadata_tree(root: MetadataTreeNode) -> list[FilesystemMetadataRecord]:
    out: list[FilesystemMetadataRecord] = []

    def visit(node: MetadataTreeNode) -> None:
        for child in node.children:
            out.append(
                FilesystemMetadataRecord(
                    frn=int(child.frn),
                    parent_frn=int(child.parent_frn),
                    name=str(child.name or ""),
                    is_dir=bool(child.is_dir),
                    size=int(child.size or 0),
                )
            )
            visit(child)

    visit(root)
    return out


def _roll_up_sizes(node: MetadataTreeNode) -> int:
    if not node.is_dir:
        return int(node.size or 0)
    total = 0
    for child in node.children:
        total += _roll_up_sizes(child)
    node.size = int(total)
    return node.size


def _sort_children(node: MetadataTreeNode) -> None:
    node.children.sort(key=lambda child: int(child.size or 0), reverse=True)
    for child in node.children:
        _sort_children(child)
