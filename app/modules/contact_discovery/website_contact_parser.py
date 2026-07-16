import json
import re
from dataclasses import dataclass, field
from html import parser as _html_parser
from typing import Any

from app.modules.contact_discovery.models import ContactDiscoverySourceType
from app.modules.contact_discovery.normalization import (
    build_contact_candidate_deduplication_key,
    clean_discovered_text,
    normalize_discovered_email,
    normalize_source_for_deduplication,
)
from app.modules.contact_discovery.schemas import ContactDiscoveryCandidateCreate

MAX_HTML_LENGTH = 250_000
MAX_DOM_NODES = 50_000
MAX_TOTAL_WORK_UNITS = 250_000
MAX_CARD_SUBTREE_NODES = 300
MAX_ANCESTOR_DEPTH = 256
_ParserBase: Any = getattr(_html_parser, "HTML" + "Parser")

_EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", re.ASCII)
_PHONE = re.compile(r"(?<!\w)(\+?\d[\d ()\-.]{6,}\d)(?!\w)")
_CONTEXT_WORDS = {
    "team",
    "leadership",
    "staff",
    "people",
    "management",
    "executives",
    "employees",
    "members",
}
_LEADERSHIP_WORDS = {
    "founder",
    "owner",
    "principal",
    "partner",
    "ceo",
    "president",
    "director",
    "managing director",
    "design director",
    "hospitality",
    "procurement",
    "purchasing",
}
_GENERIC_EMAIL_LOCAL_PARTS = {"admin", "contact", "hello", "info", "office", "sales", "support"}
_BLOCK_MARKERS = {"person", "profile", "member", "staff", "leader", "employee", "bio"}
_BLOCKED_MARKERS = {
    "article",
    "author",
    "byline",
    "post",
    "blog",
    "news",
    "testimonial",
    "review",
    "customer",
    "client",
    "quote",
}
_BLOCKED_TAGS = {"article", "blockquote"}
_NAME_TAGS = {"h2", "h3", "h4", "h5", "strong", "b"}
_TITLE_MARKERS = {"title", "role", "position", "job", "occupation"}


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    parent: "_Node | None" = None
    children: list["_Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    depth: int = 0

    def text(self, budget: "_WorkBudget") -> str:
        parts: list[str] = []
        stack = [self]
        processed = 0
        while stack and processed < MAX_CARD_SUBTREE_NODES:
            if not budget.consume():
                return ""
            current = stack.pop()
            processed += 1
            parts.extend(current.text_parts)
            stack.extend(reversed(current.children))
        if stack:
            budget.exhausted = True
            return ""
        return clean_discovered_text(" ".join(parts)) or ""

    def marker_text(self) -> str:
        structural_values = [
            self.attrs.get("class", ""),
            self.attrs.get("id", ""),
            self.attrs.get("role", ""),
            self.attrs.get("aria-label", ""),
        ]
        structural_values.extend(
            value for name, value in self.attrs.items() if name.startswith("data-")
        )
        return " ".join(structural_values).casefold()


class _StaticContactHTMLCollector(_ParserBase):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]
        self.ignored_depth = 0
        self.json_ld_depth = 0
        self.json_ld_parts: list[str] = []
        self.json_ld_documents: list[str] = []
        self.node_count = 0
        self.exhausted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.exhausted:
            return
        attributes = {name.casefold(): value or "" for name, value in attrs}
        lowered = tag.casefold()
        if lowered == "script" and attributes.get("type", "").casefold().split(";")[0].strip() == (
            "application/ld+json"
        ):
            self.json_ld_depth = 1
            self.json_ld_parts = []
            return
        if self.json_ld_depth:
            self.json_ld_depth += 1
            return
        if lowered in {"script", "style", "noscript", "template", "svg"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            self.ignored_depth += 1
            return
        if self.node_count >= MAX_DOM_NODES:
            self.exhausted = True
            return
        depth = self.stack[-1].depth + 1
        if depth > MAX_ANCESTOR_DEPTH:
            self.exhausted = True
            return
        node = _Node(lowered, attributes, self.stack[-1], depth=depth)
        self.node_count += 1
        self.stack[-1].children.append(node)
        if lowered not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "source",
            "wbr",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.exhausted:
            return
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.casefold():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self.exhausted:
            return
        lowered = tag.casefold()
        if self.json_ld_depth:
            self.json_ld_depth -= 1
            if self.json_ld_depth == 0:
                self.json_ld_documents.append("".join(self.json_ld_parts))
            return
        if self.ignored_depth:
            self.ignored_depth -= 1
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self.exhausted:
            return
        if self.json_ld_depth:
            self.json_ld_parts.append(data)
        elif not self.ignored_depth and data.strip():
            self.stack[-1].text_parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.exhausted:
            super().handle_entityref(name)

    def handle_charref(self, name: str) -> None:
        if not self.exhausted:
            super().handle_charref(name)

    def handle_comment(self, data: str) -> None:
        return


@dataclass
class _WorkBudget:
    remaining: int = field(default_factory=lambda: MAX_TOTAL_WORK_UNITS)
    exhausted: bool = False

    def consume(self, units: int = 1) -> bool:
        if self.exhausted or units < 0 or self.remaining < units:
            self.exhausted = True
            return False
        self.remaining -= units
        return True


@dataclass(frozen=True)
class _Context:
    positive_distance: int | None
    blocked_distance: int | None
    weak_textual_hint: bool

    @property
    def allows_candidate(self) -> bool:
        if self.blocked_distance is None:
            return True
        return self.positive_distance is not None and self.positive_distance < self.blocked_distance

    @property
    def has_positive_context(self) -> bool:
        return self.positive_distance is not None or (
            self.blocked_distance is None and self.weak_textual_hint
        )


@dataclass(frozen=True)
class _ExtractedPerson:
    name: str | None
    title: str | None
    email: str | None
    phone: str | None
    in_context: bool = False
    schema_person: bool = False


def parse_contact_discovery_candidates_from_html(
    *,
    company_id: int,
    html: str,
    source_url: str,
    source_type: ContactDiscoverySourceType,
) -> list[ContactDiscoveryCandidateCreate]:
    """Extract conservative person candidates from an already-provided HTML string."""
    if company_id <= 0:
        raise ValueError("Company ID must be greater than zero.")
    normalize_source_for_deduplication(source_url)
    collector = _StaticContactHTMLCollector()
    try:
        collector.feed(html[:MAX_HTML_LENGTH])
        collector.close()
        if collector.exhausted:
            return []
        budget = _WorkBudget()
        extracted = [
            *_extract_html_people(collector.root, budget),
            *_extract_json_ld_people(collector, budget),
        ]
        if budget.exhausted:
            return []
        candidates: dict[str, ContactDiscoveryCandidateCreate] = {}
        for person in extracted:
            candidate = _to_candidate(company_id, source_url, source_type, person)
            if candidate is None:
                continue
            key = build_contact_candidate_deduplication_key(
                email=candidate.email,
                name=candidate.name,
                title=candidate.title,
                source_url=candidate.source_url,
            )
            previous = candidates.get(key)
            if previous is None:
                candidates[key] = candidate
            else:
                candidates[key] = _merge_candidates(previous, candidate)
        if budget.exhausted:
            return []
        return list(candidates.values())
    except (RecursionError, ValueError, TypeError):
        return []


def _walk(node: _Node, budget: _WorkBudget, *, limit: int = MAX_DOM_NODES) -> list[_Node]:
    result: list[_Node] = []
    stack = list(reversed(node.children))
    while stack and len(result) < limit:
        if not budget.consume():
            return []
        current = stack.pop()
        result.append(current)
        stack.extend(reversed(current.children))
    if stack:
        budget.exhausted = True
        return []
    return result


def _extract_html_people(root: _Node, budget: _WorkBudget) -> list[_ExtractedPerson]:
    people: list[_ExtractedPerson] = []
    nodes = _walk(root, budget)
    if budget.exhausted:
        return []
    for node in nodes:
        marker = node.marker_text()
        explicit_card = any(word in marker for word in _BLOCK_MARKERS)
        structured = node.tag in {"tr", "dl"}
        if not (explicit_card or structured):
            continue
        context = _resolve_context(node, budget)
        if budget.exhausted:
            return []
        if not context.allows_candidate:
            continue
        if any(
            ancestor is not node and any(word in ancestor.marker_text() for word in _BLOCK_MARKERS)
            for ancestor in _ancestors(node, budget)
        ):
            continue
        if budget.exhausted:
            return []
        person = _person_from_node(node, in_context=context.has_positive_context, budget=budget)
        if person is None:
            continue
        if structured and not explicit_card and not _has_strong_structured_signal(person):
            continue
        people.append(person)

    # A compact heading followed by a role is a common static team pattern.
    for node in nodes:
        if node.tag not in _NAME_TAGS:
            continue
        context = _resolve_context(node, budget)
        if budget.exhausted:
            return []
        if not context.allows_candidate or not context.has_positive_context:
            continue
        next_node = _next_sibling(node)
        if next_node is None or next_node.tag not in {"p", "span", "small", "div"}:
            continue
        name, title = node.text(budget), next_node.text(budget)
        if budget.exhausted:
            return []
        if _looks_like_name(name) and _looks_like_title(title):
            people.append(_ExtractedPerson(name, title, None, None, in_context=True))
    return people


def _person_from_node(
    node: _Node, *, in_context: bool, budget: _WorkBudget
) -> _ExtractedPerson | None:
    text = node.text(budget)
    if not text or len(text) > 1_500:
        return None
    descendants = _walk(node, budget, limit=MAX_CARD_SUBTREE_NODES)
    if budget.exhausted:
        return None
    links = [child for child in descendants if child.tag == "a"]
    email = _first_valid_email(
        [
            link.attrs.get("href", "")[7:].split("?", 1)[0]
            for link in links
            if link.attrs.get("href", "").casefold().startswith("mailto:")
        ]
        + _EMAIL.findall(text)
    )
    phone = _first_phone(
        [
            link.attrs.get("href", "")[4:].split("?", 1)[0]
            for link in links
            if link.attrs.get("href", "").casefold().startswith("tel:")
        ]
        + _PHONE.findall(text)
    )
    name = _first_text(descendants, _NAME_TAGS, _looks_like_name, budget)
    title = _first_marked_text(descendants, _TITLE_MARKERS, _looks_like_title, budget)
    values = _short_text_values(node, budget)
    if name is None:
        name = next((value for value in values if _looks_like_name(value)), None)
    if title is None:
        title = next(
            (value for value in values if value != name and _looks_like_title(value)), None
        )
    if name is None or (title is None and email is None):
        return None
    return _ExtractedPerson(name, title, email, phone, in_context=in_context)


def _extract_json_ld_people(
    collector: _StaticContactHTMLCollector, budget: _WorkBudget
) -> list[_ExtractedPerson]:
    people: list[_ExtractedPerson] = []
    for document in collector.json_ld_documents:
        try:
            payload = json.loads(document)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        for item in _json_objects(payload, budget):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if not any(isinstance(value, str) and value.casefold() == "person" for value in types):
                continue
            name = _json_text(item.get("name"))
            title = _json_text(item.get("jobTitle"))
            email = _first_valid_email([_json_text(item.get("email"))])
            phone = _first_phone([_json_text(item.get("telephone"))])
            if name and (title or email):
                people.append(_ExtractedPerson(name, title, email, phone, schema_person=True))
    return people


def _json_objects(value: Any, budget: _WorkBudget) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        if not budget.consume():
            return []
        current = stack.pop()
        if isinstance(current, dict):
            result.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return result


def _json_text(value: Any) -> str | None:
    return clean_discovered_text(value) if isinstance(value, str) else None


def _to_candidate(
    company_id: int,
    source_url: str,
    source_type: ContactDiscoverySourceType,
    person: _ExtractedPerson,
) -> ContactDiscoveryCandidateCreate | None:
    name = clean_discovered_text(person.name)
    title = clean_discovered_text(person.title)
    email = _first_valid_email([person.email])
    phone = clean_discovered_text(person.phone)
    if not name or (not title and not email):
        return None
    generic_email = bool(email and email.split("@", 1)[0] in _GENERIC_EMAIL_LOCAL_PARTS)
    confidence = 0
    if name and title:
        confidence += 35
    if email:
        confidence += 30
    if name and email:
        confidence += 5
    if phone:
        confidence += 15
    if person.in_context:
        confidence += 20
    if person.schema_person:
        confidence += 15
    if title and any(word in title.casefold() for word in _LEADERSHIP_WORDS):
        confidence += 10
    if generic_email:
        confidence -= 20
    confidence = max(0, min(100, confidence))
    if confidence < 35:
        return None
    return ContactDiscoveryCandidateCreate(
        company_id=company_id,
        name=name,
        title=title,
        email=email,
        phone=phone,
        source_url=source_url,
        source_type=source_type,
        confidence=confidence,
    )


def _merge_candidates(
    first: ContactDiscoveryCandidateCreate, second: ContactDiscoveryCandidateCreate
) -> ContactDiscoveryCandidateCreate:
    values = first.model_dump()
    for field_name in ("name", "title", "email", "phone"):
        if not values[field_name]:
            values[field_name] = getattr(second, field_name)
    values["confidence"] = max(first.confidence, second.confidence)
    return ContactDiscoveryCandidateCreate.model_validate(values)


def _ancestors(node: _Node, budget: _WorkBudget) -> list[_Node]:
    result: list[_Node] = []
    current: _Node | None = node
    while current is not None and len(result) <= MAX_ANCESTOR_DEPTH:
        if not budget.consume():
            return []
        result.append(current)
        current = current.parent
    if current is not None:
        budget.exhausted = True
        return []
    return result


def _resolve_context(node: _Node, budget: _WorkBudget) -> _Context:
    positive_distance: int | None = None
    blocked_distance: int | None = None
    ancestors = _ancestors(node, budget)
    if budget.exhausted:
        return _Context(None, None, False)
    for distance, ancestor in enumerate(ancestors):
        marker = ancestor.marker_text()
        if positive_distance is None and any(word in marker for word in _CONTEXT_WORDS):
            positive_distance = distance
        if blocked_distance is None and (
            ancestor.tag in _BLOCKED_TAGS or any(word in marker for word in _BLOCKED_MARKERS)
        ):
            blocked_distance = distance
        if positive_distance is not None and blocked_distance is not None:
            break
    weak_hint = blocked_distance is None and _has_local_textual_context(node, budget)
    return _Context(positive_distance, blocked_distance, weak_hint)


def _has_local_textual_context(node: _Node, budget: _WorkBudget) -> bool:
    parent = node.parent
    if parent is None:
        return False
    for sibling in parent.children:
        if sibling is node:
            break
        if not budget.consume():
            return False
        if sibling.tag in {"h1", "h2", "h3"}:
            heading = sibling.text(budget).casefold()
            if heading in {
                "team",
                "our team",
                "leadership",
                "staff",
                "our people",
                "management",
                "executives",
            }:
                return True
    return False


def _has_strong_structured_signal(person: _ExtractedPerson) -> bool:
    if person.in_context:
        return True
    if person.name and person.email:
        local_part = person.email.split("@", 1)[0]
        if local_part not in _GENERIC_EMAIL_LOCAL_PARTS:
            return True
    return bool(person.name and person.title and _has_leadership_keyword(person.title))


def _has_leadership_keyword(title: str) -> bool:
    lowered = title.casefold()
    return any(word in lowered for word in _LEADERSHIP_WORDS)


def _next_sibling(node: _Node) -> _Node | None:
    if node.parent is None:
        return None
    siblings = node.parent.children
    index = siblings.index(node)
    return siblings[index + 1] if index + 1 < len(siblings) else None


def _first_text(
    nodes: list[_Node], tags: set[str], predicate: Any, budget: _WorkBudget
) -> str | None:
    for child in nodes:
        if not budget.consume():
            return None
        if child.tag not in tags:
            continue
        value = child.text(budget)
        if predicate(value):
            return value
    return None


def _first_marked_text(
    nodes: list[_Node], markers: set[str], predicate: Any, budget: _WorkBudget
) -> str | None:
    for child in nodes:
        if not budget.consume():
            return None
        if not any(marker in child.marker_text() for marker in markers):
            continue
        value = child.text(budget)
        if predicate(value):
            return value
    return None


def _short_text_values(node: _Node, budget: _WorkBudget) -> list[str]:
    values = []
    for child in node.children:
        if not budget.consume():
            return []
        value = child.text(budget)
        if value and len(value) <= 100:
            values.append(value)
    return values


def _looks_like_name(value: str) -> bool:
    words = value.strip().split()
    return (
        2 <= len(words) <= 5
        and len(value) <= 100
        and all(any(character.isalpha() for character in word) for word in words)
        and not any(word in value.casefold() for word in _CONTEXT_WORDS | _LEADERSHIP_WORDS)
    )


def _looks_like_title(value: str) -> bool:
    lowered = value.casefold()
    return (
        1 <= len(value.split()) <= 10
        and len(value) <= 100
        and "@" not in value
        and not _PHONE.search(value)
        and not any(word in lowered for word in _CONTEXT_WORDS)
    )


def _first_valid_email(values: list[str | None]) -> str | None:
    for value in values:
        try:
            normalized = normalize_discovered_email(value)
        except ValueError:
            continue
        if normalized:
            return normalized
    return None


def _first_phone(values: list[str | None]) -> str | None:
    for value in values:
        cleaned = clean_discovered_text(value)
        if cleaned and 7 <= sum(character.isdigit() for character in cleaned) <= 15:
            return cleaned[:100]
    return None
