from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

import networkx as nx
import rdflib
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network
from rdflib import BNode, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD


# ============================================================
# Streamlit configuration
# ============================================================

st.set_page_config(
    page_title="Ontology Explorer V8.1 — Cloud",
    page_icon="🧩",
    layout="wide",
)


# ============================================================
# Visual configuration
# ============================================================

COLORS = {
    "general": "#2563EB",
    "actor": "#77B255",
    "institutional_actor": "#5B8C5A",
    "resource": "#EAB308",
    "flow": "#4F6D7A",
    "outcome": "#F59E0B",
    "context": "#94A3B8",
    "concept": "#7C8DB5",
    "datatype": "#A78BFA",
    "individual": "#F59E0B",
    "custom_instance": "#EC4899",
    "external": "#64748B",
}

EDGE_COLORS = {
    "subclass": "#475569",
    "object_property": "#0F766E",
    "datatype_property": "#7C3AED",
    "restriction": "#DC2626",
    "instance": "#B45309",
    "custom_relation": "#DB2777",
}


# ============================================================
# English visualization labels
# ============================================================

ENGLISH_LABELS = {
    # Core classes
    "GeneralNode": "General Node",
    "FoodSystemActor": "Food System Actor",
    "Resource": "Resource",
    "Flow": "Flow",
    "OneHealthOutcome": "One Health Outcome",
    "SystemContext": "System Context",

    # Actor classes
    "InputManufacturer": "Input Manufacturer",
    "Producer": "Producer",
    "FoodCompany": "Food Company",
    "RetailFoodService": "Retail / Food Service",
    "Consumer": "Consumer",
    "InstitutionalActor": "Institutional Actor",
    "EducationActor": "Education",
    "Government": "Government",
    "FinanceActor": "Finance",
    "NGO": "NGO",

    # Resource classes
    "InputResource": "Input Resource",
    "PrimaryFoodAndBiomass": "Primary Food and Biomass",
    "FoodProduct": "Food Product",
    "FoodOnReference": "FoodOn Reference",

    # Flow classes
    "ProductFlow": "Product Flow",
    "InfluenceDemandFlow": "Influence / Demand Flow",

    # One Health outcomes
    "HumanHealthNutritionOutcome": "Human Health / Nutrition Outcome",
    "EnvironmentalNaturalResourceOutcome": "Environmental / Natural Resource Outcome",

    # Context classes
    "RegulatoryInstitutionalSocialEnvironment": "Regulatory, Institutional and Social Environment",
    "FoodEnvironment": "Food Environment",

    # Object properties
    "hasSource": "has source",
    "hasTarget": "has target",
    "hasResource": "has resource",
    "interactsWith": "interacts with",
    "isInterdependentWith": "is interdependent with",
    "operatesInContext": "operates in context",
    "linkedToOutcome": "linked to outcome",
    "hasFoodOnReference": "has FoodOn reference",

    # Datatype properties
    "Name": "Name",
    "NodeID": "Node ID",
    "Latitude": "Latitude",
    "Longitude": "Longitude",
    "Date": "Date",
    "Unit": "Unit",
    "Volume": "Volume",
}

KNOWN_ACTOR_CLASSES = {
    "FoodSystemActor",
    "InputManufacturer",
    "Producer",
    "FoodCompany",
    "RetailFoodService",
    "Consumer",
}

KNOWN_INSTITUTIONAL_ACTOR_CLASSES = {
    "InstitutionalActor",
    "EducationActor",
    "Government",
    "FinanceActor",
    "NGO",
}

KNOWN_FLOW_CLASSES = {
    "Flow",
    "ProductFlow",
    "InfluenceDemandFlow",
}

KNOWN_RESOURCE_CLASSES = {
    "Resource",
    "InputResource",
    "PrimaryFoodAndBiomass",
    "FoodProduct",
}

KNOWN_OUTCOME_CLASSES = {
    "OneHealthOutcome",
    "HumanHealthNutritionOutcome",
    "EnvironmentalNaturalResourceOutcome",
}

KNOWN_CONTEXT_CLASSES = {
    "SystemContext",
    "RegulatoryInstitutionalSocialEnvironment",
    "FoodEnvironment",
}



# ============================================================
# Command-line configuration
# ============================================================


def parse_runtime_args() -> argparse.Namespace:
    """Parse arguments passed after `streamlit run ... --`."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--ttl",
        type=str,
        default=None,
        help="Path to the ontology TTL file.",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=None,
        help="Optional path to the user-extension TTL overlay.",
    )
    parser.add_argument(
        "--proposal",
        type=str,
        default=None,
        help="Optional path to the proposal-history JSON file.",
    )

    args, _ = parser.parse_known_args(sys.argv[1:])
    return args


RUNTIME_ARGS = parse_runtime_args()


# ============================================================
# Proposal display state
# ============================================================


LABEL_OVERRIDES: dict[str, str] = {}


# ============================================================
# General helpers
# ============================================================


def local_name(value: rdflib.term.Node | None) -> str:
    """Return the compact local name of an RDF node."""

    if value is None:
        return "Unknown"

    if isinstance(value, Literal):
        return str(value)

    text = str(value)

    if "#" in text:
        return text.rsplit("#", 1)[-1]

    return text.rstrip("/").rsplit("/", 1)[-1]


def humanize_identifier(value: str) -> str:
    """Convert a technical identifier into a readable English label."""

    if value in ENGLISH_LABELS:
        return ENGLISH_LABELS[value]

    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return " ".join(value.split()).strip()


def english_label(value: rdflib.term.Node | None) -> str:
    """Return the preferred English visualization label."""

    if value is not None:
        override = LABEL_OVERRIDES.get(str(value))
        if override:
            return override

    return humanize_identifier(local_name(value))


def rdf_node_id(value: rdflib.term.Node) -> str:
    """Return a stable graph identifier."""

    return str(value)


def slugify(value: str) -> str:
    """Create a URI-friendly local identifier."""

    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = value.strip("_")
    return value or "node"


def get_query_value(key: str, default: str) -> str:
    """Read one query parameter across recent Streamlit versions."""

    try:
        value = st.query_params.get(key, default)
    except Exception:
        value = default

    if isinstance(value, list):
        return value[0] if value else default

    return str(value)



# ============================================================
# Tutorial download
# ============================================================


@st.cache_data(show_spinner=False)
def load_tutorial_video(video_path: str) -> bytes:
    """Load the bundled GUI tutorial video for browser download."""

    return Path(video_path).read_bytes()


def render_tutorial_download() -> None:
    """Render a link to the GUI tutorial video hosted on Google Drive."""

    tutorial_url = (
        "https://drive.google.com/uc?"
        "export=download&id=1olrjhO2Ntd1ALDcmgS2E37IFpM0pY63T"
    )

    st.sidebar.subheader("Tutorial")

    st.sidebar.link_button(
        "Download GUI video tutorial",
        tutorial_url,
        use_container_width=True,
        help="Download the MP4 tutorial for the graphical ontology interface.",
    )


# ============================================================
# Cloud project workspace
# ============================================================


def safe_uploaded_filename(name: str) -> str:
    """Return a conservative filename for an uploaded Turtle document."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "ontology.ttl")
    if not cleaned.lower().endswith(".ttl"):
        cleaned += ".ttl"
    return cleaned


def uploaded_project_paths(uploaded_file) -> tuple[Path, Path, Path]:
    """
    Create or reuse a private temporary workspace for the current Streamlit session.

    The application repository contains no default ontology. The uploaded ontology
    is copied to a temporary server-side session directory only so RDFLib and the
    existing V8 code can work with normal Path objects.
    """

    payload = uploaded_file.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    filename = safe_uploaded_filename(uploaded_file.name)

    previous_digest = st.session_state.get("project_digest")

    if previous_digest != digest:
        workspace = Path(tempfile.mkdtemp(prefix="ontology_explorer_v8_"))
        ttl_path = workspace / filename
        ttl_path.write_bytes(payload)

        st.session_state["project_digest"] = digest
        st.session_state["project_workspace"] = str(workspace)
        st.session_state["project_ttl_path"] = str(ttl_path)
        st.session_state["project_filename"] = filename
        st.session_state["project_extension_path"] = str(
            workspace / f"{Path(filename).stem}_user_extensions.ttl"
        )
        st.session_state["project_proposal_path"] = str(
            workspace / f"{Path(filename).stem}_proposal_history.json"
        )

        LABEL_OVERRIDES.clear()
        st.cache_resource.clear()

    return (
        Path(st.session_state["project_ttl_path"]),
        Path(st.session_state["project_extension_path"]),
        Path(st.session_state["project_proposal_path"]),
    )


def current_project_name() -> str:
    """Return the uploaded ontology filename shown in the interface."""

    return st.session_state.get("project_filename", "Uploaded ontology")


def reset_uploaded_project() -> None:
    """Forget the current uploaded project from Streamlit session state."""

    for key in (
        "project_digest",
        "project_workspace",
        "project_ttl_path",
        "project_filename",
        "project_extension_path",
        "project_proposal_path",
    ):
        st.session_state.pop(key, None)

    LABEL_OVERRIDES.clear()
    st.cache_resource.clear()

    try:
        st.query_params.clear()
    except Exception:
        pass


def go_to_view(view: str) -> None:
    """Navigate between Explorer and Editor without opening a second browser tab."""

    st.query_params["view"] = view
    st.rerun()


def project_downloads(
    extension_path: Path,
    proposal_path: Path,
) -> None:
    """Offer server-side session files for download when they exist."""

    st.caption(
        "Cloud sessions are temporary. Download any generated files you want "
        "to keep before closing or resetting the app."
    )

    if extension_path.exists() and extension_path.stat().st_size > 0:
        st.download_button(
            "Download user extensions TTL",
            data=extension_path.read_bytes(),
            file_name=extension_path.name,
            mime="text/turtle",
            use_container_width=True,
        )

    if proposal_path.exists() and proposal_path.stat().st_size > 0:
        st.download_button(
            "Download proposal history JSON",
            data=proposal_path.read_bytes(),
            file_name=proposal_path.name,
            mime="application/json",
            use_container_width=True,
        )


# ============================================================
# TTL paths and loading
# ============================================================


def discover_default_ttl() -> str:
    """Try to find a TTL file near the application."""

    project_root = Path(__file__).resolve().parent
    candidates = list(project_root.glob("*.ttl"))

    data_dir = project_root / "data"
    if data_dir.exists():
        candidates.extend(data_dir.glob("*.ttl"))

    candidates = [
        path
        for path in candidates
        if not path.name.endswith("_user_extensions.ttl")
    ]

    if candidates:
        return str(sorted(candidates)[0])

    return str(project_root / "ontology.ttl")


def resolve_ttl_path() -> Path:
    """Resolve the base TTL path from CLI or fallback discovery."""

    if RUNTIME_ARGS.ttl:
        return Path(RUNTIME_ARGS.ttl).expanduser().resolve()

    return Path(discover_default_ttl()).expanduser().resolve()


def resolve_extension_path(ttl_path: Path) -> Path:
    """Resolve the extension overlay path."""

    if RUNTIME_ARGS.extensions:
        return Path(RUNTIME_ARGS.extensions).expanduser().resolve()

    return ttl_path.with_name(
        f"{ttl_path.stem}_user_extensions.ttl"
    )


def resolve_proposal_path(ttl_path: Path) -> Path:
    """Resolve the proposal-history path."""

    if RUNTIME_ARGS.proposal:
        return Path(RUNTIME_ARGS.proposal).expanduser().resolve()

    return ttl_path.with_name(
        f"{ttl_path.stem}_proposal_history.json"
    )


@st.cache_resource
def load_base_ontology(ttl_path: str) -> rdflib.Graph:
    """Load a Turtle ontology."""

    graph = rdflib.Graph()
    graph.parse(ttl_path, format="turtle")
    return graph


def load_extension_graph(extension_path: Path) -> rdflib.Graph:
    """Load the optional user extension overlay."""

    graph = rdflib.Graph()

    if extension_path.exists() and extension_path.stat().st_size > 0:
        graph.parse(str(extension_path), format="turtle")

    return graph


def combined_graph(
    base_graph: rdflib.Graph,
    extension_graph: rdflib.Graph,
) -> rdflib.Graph:
    """Return a merged in-memory graph without modifying the source TTL."""

    graph = rdflib.Graph()

    for prefix, namespace in base_graph.namespaces():
        graph.bind(prefix, namespace)

    for prefix, namespace in extension_graph.namespaces():
        graph.bind(prefix, namespace)

    for triple in base_graph:
        graph.add(triple)

    for triple in extension_graph:
        graph.add(triple)

    return graph


def ontology_namespace(graph: rdflib.Graph, ttl_path: Path) -> Namespace:
    """Infer a namespace for newly created extension resources."""

    ontology_uris = [
        subject
        for subject in graph.subjects(RDF.type, OWL.Ontology)
        if isinstance(subject, URIRef)
    ]

    if ontology_uris:
        base = str(ontology_uris[0]).rstrip("#/")
        return Namespace(base + "/user/")

    return Namespace(f"urn:ontology:{ttl_path.stem}:user:")


# ============================================================
# Ontology extraction
# ============================================================


def get_classes(graph: rdflib.Graph) -> set[URIRef]:
    """Return all named OWL classes."""

    return {
        subject
        for subject in graph.subjects(RDF.type, OWL.Class)
        if isinstance(subject, URIRef)
    }


def get_object_properties(graph: rdflib.Graph) -> set[URIRef]:
    """Return all named object properties."""

    return {
        subject
        for subject in graph.subjects(RDF.type, OWL.ObjectProperty)
        if isinstance(subject, URIRef)
    }


def get_datatype_properties(graph: rdflib.Graph) -> set[URIRef]:
    """Return all named datatype properties."""

    return {
        subject
        for subject in graph.subjects(RDF.type, OWL.DatatypeProperty)
        if isinstance(subject, URIRef)
    }


def get_individuals(graph: rdflib.Graph) -> set[URIRef]:
    """Return all named individuals."""

    return {
        subject
        for subject in graph.subjects(RDF.type, OWL.NamedIndividual)
        if isinstance(subject, URIRef)
    }


def get_named_subclasses(
    graph: rdflib.Graph,
) -> list[tuple[URIRef, URIRef]]:
    """Return direct named subclass relations."""

    relations = []

    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            relations.append((child, parent))

    return relations


def get_property_domains(
    graph: rdflib.Graph,
    prop: URIRef,
) -> list[URIRef]:
    """Return named domains of a property."""

    return [
        value
        for value in graph.objects(prop, RDFS.domain)
        if isinstance(value, URIRef)
    ]


def get_property_ranges(
    graph: rdflib.Graph,
    prop: URIRef,
) -> list[URIRef]:
    """Return named ranges of a property."""

    return [
        value
        for value in graph.objects(prop, RDFS.range)
        if isinstance(value, URIRef)
    ]


def is_functional_property(
    graph: rdflib.Graph,
    prop: URIRef,
) -> bool:
    """Check whether a property is declared functional."""

    return (prop, RDF.type, OWL.FunctionalProperty) in graph


def descendants_of(
    graph: rdflib.Graph,
    parent_uri: URIRef,
) -> set[URIRef]:
    """Return named transitive subclasses of one class."""

    children_by_parent = defaultdict(set)

    for child, parent in get_named_subclasses(graph):
        children_by_parent[parent].add(child)

    found = set()
    frontier = [parent_uri]

    while frontier:
        parent = frontier.pop()
        for child in children_by_parent.get(parent, set()):
            if child not in found:
                found.add(child)
                frontier.append(child)

    return found


def find_general_node_class(graph: rdflib.Graph) -> URIRef | None:
    """Find a likely General Node class."""

    classes = get_classes(graph)

    for class_uri in classes:
        normalized = re.sub(r"[^a-z]", "", local_name(class_uri).lower())
        if normalized == "generalnode":
            return class_uri

    return None


def find_actor_root_class(graph: rdflib.Graph) -> URIRef | None:
    """Find the preferred root class for food-system actor instances."""

    classes = get_classes(graph)

    preferred_names = ("foodsystemactor", "generalnode")
    normalized_lookup = {
        re.sub(r"[^a-z]", "", local_name(class_uri).lower()): class_uri
        for class_uri in classes
    }

    for preferred_name in preferred_names:
        if preferred_name in normalized_lookup:
            return normalized_lookup[preferred_name]

    return None


def is_descendant_or_self(
    graph: rdflib.Graph,
    class_uri: URIRef,
    root_uri: URIRef | None,
) -> bool:
    """Return whether a class is the given root or one of its named descendants."""

    if root_uri is None:
        return False

    return class_uri == root_uri or class_uri in descendants_of(graph, root_uri)


# ============================================================
# OWL restrictions
# ============================================================


def parse_restriction(
    graph: rdflib.Graph,
    restriction_node: BNode,
) -> dict | None:
    """Parse a supported OWL restriction."""

    prop = graph.value(restriction_node, OWL.onProperty)

    if prop is None:
        return None

    result = {
        "property": prop,
        "kind": None,
        "cardinality": None,
        "target": None,
    }

    exact = graph.value(restriction_node, OWL.qualifiedCardinality)
    minimum = graph.value(restriction_node, OWL.minQualifiedCardinality)
    maximum = graph.value(restriction_node, OWL.maxQualifiedCardinality)
    some_values = graph.value(restriction_node, OWL.someValuesFrom)
    all_values = graph.value(restriction_node, OWL.allValuesFrom)

    if exact is not None:
        result["kind"] = "exact"
        result["cardinality"] = int(str(exact))
    elif minimum is not None:
        result["kind"] = "min"
        result["cardinality"] = int(str(minimum))
    elif maximum is not None:
        result["kind"] = "max"
        result["cardinality"] = int(str(maximum))
    elif some_values is not None:
        result["kind"] = "some"
        result["target"] = some_values
    elif all_values is not None:
        result["kind"] = "only"
        result["target"] = all_values

    if result["target"] is None:
        result["target"] = (
            graph.value(restriction_node, OWL.onClass)
            or graph.value(restriction_node, OWL.onDataRange)
        )

    return result


def get_class_restrictions(
    graph: rdflib.Graph,
) -> dict[URIRef, list[dict]]:
    """Collect restrictions attached to named classes."""

    restrictions = defaultdict(list)

    for class_uri, parent in graph.subject_objects(RDFS.subClassOf):
        if not isinstance(class_uri, URIRef):
            continue
        if not isinstance(parent, BNode):
            continue
        if (parent, RDF.type, OWL.Restriction) not in graph:
            continue

        parsed = parse_restriction(graph, parent)
        if parsed is not None:
            restrictions[class_uri].append(parsed)

    return dict(restrictions)


def restriction_cardinality_label(
    graph: rdflib.Graph,
    restriction: dict,
) -> str:
    """Convert an OWL restriction into a compact human label."""

    kind = restriction.get("kind")
    value = restriction.get("cardinality")
    prop = restriction.get("property")

    functional = (
        isinstance(prop, URIRef)
        and is_functional_property(graph, prop)
    )

    if kind == "exact":
        return f"[{value}]"
    if kind == "min":
        if value == 1 and functional:
            return "[1]"
        return f"[{value}..*]"
    if kind == "max":
        return f"[0..{value}]"
    if kind == "some":
        return "[1]" if functional else "[1..*]"
    if kind == "only":
        return "[only]"
    if functional:
        return "[0..1]"

    return ""


def restriction_explanation(
    graph: rdflib.Graph,
    restriction: dict,
) -> str:
    """Generate a readable explanation of a restriction."""

    prop_label = english_label(restriction.get("property"))
    target = english_label(restriction.get("target"))
    cardinality = restriction_cardinality_label(graph, restriction)

    if cardinality:
        return f"{prop_label} {cardinality} → {target}"

    return f"{prop_label} → {target}"


# ============================================================
# Enumeration parsing
# ============================================================


def read_rdf_list(
    graph: rdflib.Graph,
    head: rdflib.term.Node,
) -> list[rdflib.term.Node]:
    """Read an RDF collection."""

    values = []
    current = head

    while current and current != RDF.nil:
        first = graph.value(current, RDF.first)
        if first is not None:
            values.append(first)
        current = graph.value(current, RDF.rest)

    return values


def get_class_enumerations(
    graph: rdflib.Graph,
) -> dict[URIRef, list[rdflib.term.Node]]:
    """Return owl:oneOf values attached to named classes."""

    result = {}

    for class_uri in get_classes(graph):
        for equivalent in graph.objects(class_uri, OWL.equivalentClass):
            if not isinstance(equivalent, BNode):
                continue

            one_of = graph.value(equivalent, OWL.oneOf)
            if one_of is None:
                continue

            result[class_uri] = read_rdf_list(graph, one_of)

    return result


# ============================================================
# Semantic class categories
# ============================================================


def class_category(
    graph: rdflib.Graph,
    class_uri: URIRef,
) -> str:
    """Assign a visual family to a class using ontology structure first."""

    name = local_name(class_uri)
    general_node = find_general_node_class(graph)

    if general_node is not None and class_uri == general_node:
        return "general"

    classes_by_name = {local_name(value): value for value in get_classes(graph)}

    institutional_root = classes_by_name.get("InstitutionalActor")
    actor_root = classes_by_name.get("FoodSystemActor")
    resource_root = classes_by_name.get("Resource")
    flow_root = classes_by_name.get("Flow")
    outcome_root = classes_by_name.get("OneHealthOutcome")
    context_root = classes_by_name.get("SystemContext")

    if name in KNOWN_INSTITUTIONAL_ACTOR_CLASSES or is_descendant_or_self(
        graph, class_uri, institutional_root
    ):
        return "institutional_actor"

    if name in KNOWN_ACTOR_CLASSES or is_descendant_or_self(
        graph, class_uri, actor_root
    ):
        return "actor"

    if name in KNOWN_FLOW_CLASSES or is_descendant_or_self(
        graph, class_uri, flow_root
    ):
        return "flow"

    if name in KNOWN_RESOURCE_CLASSES or is_descendant_or_self(
        graph, class_uri, resource_root
    ):
        return "resource"

    if name in KNOWN_OUTCOME_CLASSES or is_descendant_or_self(
        graph, class_uri, outcome_root
    ):
        return "outcome"

    if name in KNOWN_CONTEXT_CLASSES or is_descendant_or_self(
        graph, class_uri, context_root
    ):
        return "context"

    if name == "FoodOnReference":
        return "external"

    return "concept"


# ============================================================
# Tooltip construction
# ============================================================


def build_class_tooltip(
    graph: rdflib.Graph,
    class_uri: URIRef,
    restrictions: dict[URIRef, list[dict]],
    enumerations: dict[URIRef, list[rdflib.term.Node]],
    detailed: bool,
) -> str:
    """Build readable plain-text class information."""

    lines = [
        english_label(class_uri),
        "",
        "Ontology class",
    ]

    parents = [
        parent
        for child, parent in get_named_subclasses(graph)
        if child == class_uri
    ]

    if parents:
        lines.extend(["", "Subclass of:"])
        for parent in parents:
            lines.append(f"• {english_label(parent)}")

    class_restrictions = restrictions.get(class_uri, [])

    if class_restrictions:
        lines.extend(["", "Constraints:"])
        for restriction in class_restrictions:
            lines.append(
                "• " + restriction_explanation(graph, restriction)
            )

    values = enumerations.get(class_uri, [])

    if values:
        lines.extend(["", "Allowed values:"])
        for value in values:
            lines.append(f"• {english_label(value)}")

    if detailed:
        lines.extend(["", "OWL URI:", str(class_uri)])

    return "\n".join(lines)


# ============================================================
# Schema graph construction
# ============================================================


def build_ontology_graph(
    rdf_graph: rdflib.Graph,
    complexity: int,
    detailed_mode: bool,
    show_attributes: bool,
) -> nx.MultiDiGraph:
    """
    Build the progressive ontology visualization.

    Complexity levels:
    1 - classes
    2 - inheritance
    3 - object properties
    4 - datatype properties
    5 - cardinalities / restrictions
    6 - individuals and semantic detail
    """

    graph = nx.MultiDiGraph()
    classes = get_classes(rdf_graph)
    object_properties = get_object_properties(rdf_graph)
    datatype_properties = get_datatype_properties(rdf_graph)
    restrictions = get_class_restrictions(rdf_graph)
    enumerations = get_class_enumerations(rdf_graph)

    # Level 1: classes
    for class_uri in classes:
        category = class_category(rdf_graph, class_uri)

        graph.add_node(
            rdf_node_id(class_uri),
            label=english_label(class_uri),
            node_type="class",
            category=category,
            uri=str(class_uri),
            title=build_class_tooltip(
                graph=rdf_graph,
                class_uri=class_uri,
                restrictions=restrictions,
                enumerations=enumerations,
                detailed=detailed_mode,
            ),
        )

    # Level 2: inheritance
    if complexity >= 2:
        for child, parent in get_named_subclasses(rdf_graph):
            if rdf_node_id(child) not in graph:
                continue
            if rdf_node_id(parent) not in graph:
                continue

            graph.add_edge(
                rdf_node_id(parent),
                rdf_node_id(child),
                relation_type="subclass",
                label="is a",
                title=(
                    f"{english_label(child)} is a type of "
                    f"{english_label(parent)}"
                ),
            )

    # Level 3: object properties
    if complexity >= 3:
        for prop in object_properties:
            domains = get_property_domains(rdf_graph, prop)
            ranges = get_property_ranges(rdf_graph, prop)

            for domain in domains:
                for range_value in ranges:
                    if rdf_node_id(domain) not in graph:
                        continue
                    if rdf_node_id(range_value) not in graph:
                        continue

                    label = english_label(prop)
                    cardinality = ""

                    if complexity >= 5:
                        matching_restrictions = [
                            restriction
                            for restriction in restrictions.get(domain, [])
                            if restriction.get("property") == prop
                            and restriction.get("target") == range_value
                        ]

                        if matching_restrictions:
                            cardinality = restriction_cardinality_label(
                                rdf_graph,
                                matching_restrictions[0],
                            )
                        elif is_functional_property(rdf_graph, prop):
                            cardinality = "[0..1]"

                    visible_label = label
                    if cardinality:
                        visible_label += f" {cardinality}"

                    graph.add_edge(
                        rdf_node_id(domain),
                        rdf_node_id(range_value),
                        relation_type="object_property",
                        label=visible_label,
                        title=(
                            f"{english_label(domain)}\n"
                            f"{label}\n"
                            f"{english_label(range_value)}"
                            + (
                                f"\nCardinality: {cardinality}"
                                if cardinality
                                else ""
                            )
                        ),
                    )

    # Level 4: datatype properties
    if complexity >= 4 and show_attributes:
        for prop in datatype_properties:
            domains = get_property_domains(rdf_graph, prop)
            ranges = get_property_ranges(rdf_graph, prop)

            if not domains:
                continue

            for domain in domains:
                if rdf_node_id(domain) not in graph:
                    continue

                range_value = ranges[0] if ranges else None
                attribute_id = f"attribute::{str(prop)}::{str(domain)}"
                cardinality = ""

                if complexity >= 5:
                    candidates = [
                        restriction
                        for restriction in restrictions.get(domain, [])
                        if restriction.get("property") == prop
                    ]

                    if candidates:
                        cardinality = restriction_cardinality_label(
                            rdf_graph,
                            candidates[0],
                        )

                label = english_label(prop)
                if cardinality:
                    label += f" {cardinality}"

                tooltip_lines = [
                    english_label(prop),
                    "",
                    "Attribute",
                ]

                if range_value is not None:
                    tooltip_lines.append(
                        f"Type: {english_label(range_value)}"
                    )

                if cardinality:
                    tooltip_lines.append(
                        f"Cardinality: {cardinality}"
                    )

                if detailed_mode:
                    tooltip_lines.extend(["", f"OWL URI: {prop}"])

                graph.add_node(
                    attribute_id,
                    label=label,
                    node_type="attribute",
                    category="datatype",
                    title="\n".join(tooltip_lines),
                )

                graph.add_edge(
                    rdf_node_id(domain),
                    attribute_id,
                    relation_type="datatype_property",
                    label="attribute",
                    title=(
                        f"{english_label(domain)} has attribute "
                        f"{english_label(prop)}"
                    ),
                )

    # Level 5: restrictions not already represented
    if complexity >= 5:
        for class_uri, class_restrictions in restrictions.items():
            for restriction in class_restrictions:
                prop = restriction.get("property")
                target = restriction.get("target")

                if not isinstance(target, URIRef):
                    continue
                if rdf_node_id(class_uri) not in graph:
                    continue
                if rdf_node_id(target) not in graph:
                    continue

                already_exists = False

                for _, existing_target, edge_data in graph.out_edges(
                    rdf_node_id(class_uri),
                    data=True,
                ):
                    if (
                        existing_target == rdf_node_id(target)
                        and english_label(prop)
                        in edge_data.get("label", "")
                    ):
                        already_exists = True
                        break

                if already_exists:
                    continue

                graph.add_edge(
                    rdf_node_id(class_uri),
                    rdf_node_id(target),
                    relation_type="restriction",
                    label=(
                        f"{english_label(prop)} "
                        f"{restriction_cardinality_label(rdf_graph, restriction)}"
                    ).strip(),
                    title=restriction_explanation(
                        rdf_graph,
                        restriction,
                    ),
                )

    # Level 6: ontology individuals
    if complexity >= 6:
        add_named_individuals_to_graph(
            schema_graph=graph,
            rdf_graph=rdf_graph,
            detailed_mode=detailed_mode,
            only_user_extensions=False,
            extension_graph=None,
            show_fields=False,
        )

    return graph


# ============================================================
# Instance overlay
# ============================================================


def literal_display(value: Literal) -> str:
    """Format a literal for the UI."""

    return str(value)


def describe_individual(
    rdf_graph: rdflib.Graph,
    individual: URIRef,
    detailed_mode: bool,
) -> str:
    """Build a readable tooltip for one ontology individual."""

    lines = [
        english_label(individual),
        "",
        "Instance / example node",
    ]

    types = [
        value
        for value in rdf_graph.objects(individual, RDF.type)
        if isinstance(value, URIRef)
        and value not in {OWL.NamedIndividual}
    ]

    if types:
        lines.extend(["", "Type:"])
        for class_uri in types:
            lines.append(f"• {english_label(class_uri)}")

    literal_pairs = []
    object_pairs = []

    for predicate, obj in rdf_graph.predicate_objects(individual):
        if predicate == RDF.type:
            continue

        if isinstance(obj, Literal):
            literal_pairs.append((predicate, obj))
        elif isinstance(obj, URIRef):
            object_pairs.append((predicate, obj))

    if literal_pairs:
        lines.extend(["", "Fields:"])
        for predicate, value in literal_pairs:
            lines.append(
                f"• {english_label(predicate)}: {literal_display(value)}"
            )

    if object_pairs:
        lines.extend(["", "Relations:"])
        for predicate, target in object_pairs:
            lines.append(
                f"• {english_label(predicate)} → {english_label(target)}"
            )

    if detailed_mode:
        lines.extend(["", "URI:", str(individual)])

    return "\n".join(lines)


def add_named_individuals_to_graph(
    schema_graph: nx.MultiDiGraph,
    rdf_graph: rdflib.Graph,
    detailed_mode: bool,
    only_user_extensions: bool,
    extension_graph: rdflib.Graph | None,
    show_fields: bool,
) -> None:
    """Overlay ontology individuals on the conceptual schema graph."""

    if only_user_extensions and extension_graph is not None:
        individuals = get_individuals(extension_graph)
    else:
        individuals = get_individuals(rdf_graph)

    for individual in individuals:
        individual_id = rdf_node_id(individual)
        is_user_added = (
            extension_graph is not None
            and (
                individual,
                RDF.type,
                OWL.NamedIndividual,
            ) in extension_graph
        )

        category = (
            "custom_instance"
            if is_user_added
            else "individual"
        )

        schema_graph.add_node(
            individual_id,
            label=english_label(individual),
            node_type="individual",
            category=category,
            title=describe_individual(
                rdf_graph,
                individual,
                detailed_mode,
            ),
        )

        types = [
            class_uri
            for class_uri in rdf_graph.objects(individual, RDF.type)
            if isinstance(class_uri, URIRef)
            and class_uri != OWL.NamedIndividual
        ]

        for class_uri in types:
            if rdf_node_id(class_uri) not in schema_graph:
                continue

            schema_graph.add_edge(
                rdf_node_id(class_uri),
                individual_id,
                relation_type="instance",
                label="example" if not is_user_added else "instance",
                title=(
                    f"{english_label(individual)} is an instance of "
                    f"{english_label(class_uri)}"
                ),
            )

        for predicate, obj in rdf_graph.predicate_objects(individual):
            if predicate == RDF.type:
                continue

            if isinstance(obj, URIRef):
                target_id = rdf_node_id(obj)

                if target_id not in schema_graph:
                    schema_graph.add_node(
                        target_id,
                        label=english_label(obj),
                        node_type="individual",
                        category="individual",
                        title=english_label(obj),
                    )

                schema_graph.add_edge(
                    individual_id,
                    target_id,
                    relation_type="custom_relation",
                    label=english_label(predicate),
                    title=(
                        f"{english_label(individual)}\n"
                        f"{english_label(predicate)}\n"
                        f"{english_label(obj)}"
                    ),
                )

            elif isinstance(obj, Literal) and show_fields:
                field_id = (
                    f"value::{individual_id}::{str(predicate)}::{str(obj)}"
                )

                schema_graph.add_node(
                    field_id,
                    label=(
                        f"{english_label(predicate)}: "
                        f"{literal_display(obj)}"
                    ),
                    node_type="attribute",
                    category="datatype",
                    title=(
                        f"Field value\n\n"
                        f"{english_label(predicate)}: {literal_display(obj)}"
                    ),
                )

                schema_graph.add_edge(
                    individual_id,
                    field_id,
                    relation_type="datatype_property",
                    label="field",
                    title=english_label(predicate),
                )


# ============================================================
# Neighborhood filtering
# ============================================================


def filter_by_focus(
    graph: nx.MultiDiGraph,
    focus_node: str | None,
    depth: int,
) -> nx.MultiDiGraph:
    """Filter the graph to an N-hop neighborhood."""

    if focus_node is None:
        return graph.copy()

    if focus_node not in graph:
        return graph.copy()

    undirected = graph.to_undirected()
    distances = nx.single_source_shortest_path_length(
        undirected,
        focus_node,
        cutoff=depth,
    )

    return graph.subgraph(set(distances.keys())).copy()


# ============================================================
# PyVis conversion
# ============================================================


def build_pyvis(
    graph: nx.MultiDiGraph,
    height_px: int = 790,
) -> Network:
    """Convert the conceptual graph to PyVis."""

    net = Network(
        height=f"{height_px}px",
        width="100%",
        directed=True,
        bgcolor="#F8FAFC",
        font_color="#1F2937",
        cdn_resources="in_line",
    )

    for node_id_value, data in graph.nodes(data=True):
        node_type = data.get("node_type", "class")
        category = data.get("category", "concept")

        if node_type == "attribute":
            shape = "box"
            size = 17
        elif category == "custom_instance":
            shape = "star"
            size = 24
        elif node_type == "individual":
            shape = "diamond"
            size = 17
        elif category == "general":
            shape = "dot"
            size = 42
        elif category == "actor":
            shape = "dot"
            size = 32
        elif category == "institutional_actor":
            shape = "dot"
            size = 30
        elif category == "flow":
            shape = "hexagon"
            size = 32
        elif category == "outcome":
            shape = "box"
            size = 30
        elif category == "context":
            shape = "box"
            size = 28
        elif category == "resource":
            shape = "dot"
            size = 30
        else:
            shape = "dot"
            size = 27

        color = COLORS.get(category, COLORS["concept"])

        net.add_node(
            node_id_value,
            label=data.get("label", node_id_value),
            title=data.get("title", ""),
            shape=shape,
            size=size,
            color=color,
            borderWidth=2,
            font={
                "size": 16,
                "face": "Arial",
            },
        )

    for source, target, key, data in graph.edges(
        keys=True,
        data=True,
    ):
        relation_type = data.get(
            "relation_type",
            "object_property",
        )

        color = EDGE_COLORS.get(relation_type, "#64748B")
        dashed = relation_type in {
            "datatype_property",
            "restriction",
            "instance",
        }
        width = 2.8 if relation_type == "subclass" else 2.0

        net.add_edge(
            source,
            target,
            label=data.get("label", ""),
            title=data.get("title", ""),
            color=color,
            width=width,
            dashes=dashed,
            arrows="to",
            font={
                "size": 11,
                "align": "middle",
            },
        )

    net.set_options(
        """
        {
          "physics": {
            "enabled": true,
            "solver": "forceAtlas2Based",
            "forceAtlas2Based": {
              "gravitationalConstant": -75,
              "centralGravity": 0.012,
              "springLength": 190,
              "springConstant": 0.065,
              "damping": 0.65,
              "avoidOverlap": 0.8
            },
            "stabilization": {
              "enabled": true,
              "iterations": 700,
              "updateInterval": 50
            }
          },
          "interaction": {
            "hover": true,
            "navigationButtons": true,
            "keyboard": true,
            "multiselect": true
          },
          "edges": {
            "smooth": {
              "enabled": true,
              "type": "dynamic"
            }
          }
        }
        """
    )

    return net


def render_network(
    network: Network,
    component_height: int = 810,
) -> None:
    """Render PyVis with fullscreen and high-resolution snapshot controls."""

    html = network.generate_html(notebook=False)

    snapshot_controls = r"""
<style>
  #v6-graph-toolbar {
    position: fixed;
    top: 10px;
    right: 12px;
    z-index: 99999;
    display: flex;
    gap: 8px;
    padding: 7px;
    border: 1px solid rgba(148, 163, 184, 0.55);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.94);
    box-shadow: 0 4px 14px rgba(15, 23, 42, 0.14);
    backdrop-filter: blur(6px);
  }

  #v6-graph-toolbar button {
    border: 1px solid #CBD5E1;
    border-radius: 7px;
    background: #FFFFFF;
    color: #0F172A;
    padding: 7px 10px;
    font: 600 12px Arial, sans-serif;
    cursor: pointer;
  }

  #v6-graph-toolbar button:hover {
    background: #F1F5F9;
  }

  :fullscreen body {
    margin: 0 !important;
    overflow: hidden !important;
    background: #F8FAFC !important;
  }

  :fullscreen #mynetwork {
    width: 100vw !important;
    height: 100vh !important;
  }
</style>

<div id="v6-graph-toolbar">
  <button type="button" onclick="v6FitGraph()">Fit</button>
  <button type="button" onclick="v6ToggleFullscreen()">Fullscreen</button>
  <button type="button" onclick="v6Export4K()">Export 4K PNG</button>
</div>

<script>
  function v6GraphContainer() {
    return document.getElementById("mynetwork");
  }

  function v6FitGraph() {
    if (typeof network === "undefined" || !network) return;
    network.fit({ animation: false });
    network.redraw();
  }

  async function v6ToggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (error) {
      console.error("Fullscreen request failed:", error);
    }
  }

  document.addEventListener("fullscreenchange", function () {
    if (typeof network === "undefined" || !network) return;
    window.setTimeout(function () {
      network.redraw();
      network.fit({ animation: false });
    }, 120);
  });

  async function v6Export4K() {
    if (typeof network === "undefined" || !network) return;

    const container = v6GraphContainer();
    if (!container) return;

    const targetWidth = 3840;
    const targetHeight = 2160;
    const previousWidth = container.style.width;
    const previousHeight = container.style.height;
    const previousScale = network.getScale();
    const previousPosition = network.getViewPosition();

    try {
      network.stopSimulation();
      network.setSize(targetWidth + "px", targetHeight + "px");
      network.fit({ animation: false });
      network.redraw();

      await new Promise(function (resolve) {
        window.setTimeout(resolve, 250);
      });

      const sourceCanvas = container.querySelector("canvas");
      if (!sourceCanvas) {
        throw new Error("Graph canvas not found.");
      }

      const outputCanvas = document.createElement("canvas");
      outputCanvas.width = targetWidth;
      outputCanvas.height = targetHeight;

      const context = outputCanvas.getContext("2d");
      context.fillStyle = "#F8FAFC";
      context.fillRect(0, 0, targetWidth, targetHeight);
      context.drawImage(sourceCanvas, 0, 0, targetWidth, targetHeight);

      const blob = await new Promise(function (resolve) {
        outputCanvas.toBlob(resolve, "image/png", 1.0);
      });

      if (!blob) {
        throw new Error("PNG generation failed.");
      }

      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "food_system_ontology_graph_4k.png";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error("4K export failed:", error);
      alert("The 4K PNG could not be generated. See the browser console for details.");
    } finally {
      network.setSize(
        previousWidth || "100%",
        previousHeight || "790px"
      );
      network.moveTo({
        position: previousPosition,
        scale: previousScale,
        animation: false
      });
      network.redraw();
    }
  }
</script>
"""

    html = html.replace("</body>", snapshot_controls + "\n</body>")

    components.html(
        html,
        height=component_height,
        scrolling=False,
    )


# ============================================================
# Proposal history and reversible ontology operations
# ============================================================


def empty_proposal_state() -> dict:
    """Return a fresh proposal-history document."""

    return {
        "version": 1,
        "cursor": 0,
        "operations": [],
    }


def load_proposal_state(path: Path) -> dict:
    """Load proposal history from JSON."""

    if not path.exists() or path.stat().st_size == 0:
        return empty_proposal_state()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_proposal_state()

    if not isinstance(data, dict):
        return empty_proposal_state()

    operations = data.get("operations", [])
    if not isinstance(operations, list):
        operations = []

    try:
        cursor = int(data.get("cursor", len(operations)))
    except (TypeError, ValueError):
        cursor = len(operations)

    cursor = max(0, min(cursor, len(operations)))

    return {
        "version": 1,
        "cursor": cursor,
        "operations": operations,
    }


def save_proposal_state(path: Path, state: dict) -> None:
    """Persist proposal history as human-readable JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_proposal_operation(path: Path, state: dict, operation: dict) -> dict:
    """Append one operation and discard any redo branch."""

    operations = list(state.get("operations", []))
    cursor = int(state.get("cursor", len(operations)))
    operations = operations[:cursor]
    operations.append(operation)

    new_state = {
        "version": 1,
        "cursor": len(operations),
        "operations": operations,
    }
    save_proposal_state(path, new_state)
    return new_state


def set_proposal_cursor(path: Path, state: dict, cursor: int) -> dict:
    """Move the proposal cursor to support undo, redo, and time travel."""

    operations = list(state.get("operations", []))
    cursor = max(0, min(int(cursor), len(operations)))

    new_state = {
        "version": 1,
        "cursor": cursor,
        "operations": operations,
    }
    save_proposal_state(path, new_state)
    return new_state


def proposal_operation_description(operation: dict) -> str:
    """Return a concise human-readable history label."""

    kind = operation.get("operation")

    if kind == "rename_label":
        return (
            f"Rename {operation.get('before', 'concept')} → "
            f"{operation.get('after', '')}"
        )

    if kind == "add_subclass":
        return (
            f"Add subclass {operation.get('label', '')} under "
            f"{operation.get('parent_label', 'selected concept')}"
        )

    return kind or "Unknown operation"


def build_proposal_graph(
    base_graph: rdflib.Graph,
    ttl_path: Path,
    state: dict,
) -> rdflib.Graph:
    """Rebuild the active proposal layer from operation history."""

    graph = rdflib.Graph()
    namespace = ontology_namespace(base_graph, ttl_path)
    graph.bind("proposal", namespace)

    LABEL_OVERRIDES.clear()

    cursor = int(state.get("cursor", 0))
    operations = list(state.get("operations", []))[:cursor]

    for operation in operations:
        kind = operation.get("operation")

        if kind == "rename_label":
            subject = URIRef(operation["subject"])
            label = operation.get("after", "").strip()
            if label:
                LABEL_OVERRIDES[str(subject)] = label
                graph.add((subject, RDFS.label, Literal(label, lang="en")))

        elif kind == "add_subclass":
            parent = URIRef(operation["parent"])
            child = URIRef(operation["child"])
            label = operation.get("label", "").strip()

            graph.add((child, RDF.type, OWL.Class))
            graph.add((child, RDFS.subClassOf, parent))

            if label:
                graph.add((child, RDFS.label, Literal(label, lang="en")))
                LABEL_OVERRIDES[str(child)] = label

    return graph


def proposal_class_uri(
    base_graph: rdflib.Graph,
    ttl_path: Path,
    identifier: str,
) -> URIRef:
    """Create a stable URI for a proposed class."""

    namespace = ontology_namespace(base_graph, ttl_path)
    return namespace[f"class/{slugify(identifier)}"]


# ============================================================
# Editor helpers
# ============================================================


def property_is_available_for_class(
    graph: rdflib.Graph,
    prop: URIRef,
    selected_class: URIRef,
) -> bool:
    """Check whether a property domain fits the selected class."""

    domains = get_property_domains(graph, prop)

    if not domains:
        return True

    if selected_class in domains:
        return True

    parents = nx.DiGraph()
    for child, parent in get_named_subclasses(graph):
        parents.add_edge(child, parent)

    for domain in domains:
        if selected_class in parents and domain in parents:
            try:
                if nx.has_path(parents, selected_class, domain):
                    return True
            except nx.NetworkXError:
                pass

    return False


def inherited_datatype_properties(
    graph: rdflib.Graph,
    selected_class: URIRef,
) -> list[URIRef]:
    """Return datatype properties applicable to a selected class."""

    properties = [
        prop
        for prop in get_datatype_properties(graph)
        if property_is_available_for_class(graph, prop, selected_class)
    ]

    return sorted(properties, key=lambda value: english_label(value).casefold())


def inherited_object_properties(
    graph: rdflib.Graph,
    selected_class: URIRef,
) -> list[URIRef]:
    """Return object properties applicable to a selected class."""

    properties = [
        prop
        for prop in get_object_properties(graph)
        if property_is_available_for_class(graph, prop, selected_class)
    ]

    return sorted(properties, key=lambda value: english_label(value).casefold())


def parse_literal_value(
    raw_value: str,
    datatype_uri: URIRef | None,
) -> Literal:
    """Convert a form value into a typed RDF literal when possible."""

    if datatype_uri is None:
        return Literal(raw_value)

    datatype_text = str(datatype_uri)

    try:
        if datatype_text in {str(XSD.integer), str(XSD.int)}:
            return Literal(int(raw_value), datatype=datatype_uri)
        if datatype_text in {
            str(XSD.decimal),
            str(XSD.float),
            str(XSD.double),
        }:
            return Literal(float(raw_value), datatype=datatype_uri)
        if datatype_text == str(XSD.boolean):
            value = raw_value.strip().lower() in {"true", "1", "yes", "y"}
            return Literal(value, datatype=datatype_uri)
    except ValueError:
        return Literal(raw_value, datatype=datatype_uri)

    return Literal(raw_value, datatype=datatype_uri)


def ensure_custom_datatype_property(
    extension_graph: rdflib.Graph,
    namespace: Namespace,
    field_name: str,
    selected_class: URIRef,
    datatype_uri: URIRef,
) -> URIRef:
    """Create a prototype custom datatype property in the extension graph."""

    prop_uri = namespace[f"field/{slugify(field_name)}"]
    extension_graph.add((prop_uri, RDF.type, OWL.DatatypeProperty))
    extension_graph.add((prop_uri, RDFS.domain, selected_class))
    extension_graph.add((prop_uri, RDFS.range, datatype_uri))
    extension_graph.add((prop_uri, RDFS.label, Literal(field_name, lang="en")))
    return prop_uri


def ensure_custom_object_property(
    extension_graph: rdflib.Graph,
    namespace: Namespace,
    relation_name: str,
    selected_class: URIRef,
    target_class: URIRef,
) -> URIRef:
    """Create a prototype custom object property in the extension graph."""

    prop_uri = namespace[f"relation/{slugify(relation_name)}"]
    extension_graph.add((prop_uri, RDF.type, OWL.ObjectProperty))
    extension_graph.add((prop_uri, RDFS.domain, selected_class))
    extension_graph.add((prop_uri, RDFS.range, target_class))
    extension_graph.add((prop_uri, RDFS.label, Literal(relation_name, lang="en")))
    return prop_uri


def save_extension_graph(
    graph: rdflib.Graph,
    extension_path: Path,
) -> None:
    """Persist the user extension overlay without changing the source TTL."""

    extension_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(
        destination=str(extension_path),
        format="turtle",
    )


# ============================================================
# Editor page
# ============================================================


def render_node_editor(
    base_graph: rdflib.Graph,
    extension_graph: rdflib.Graph,
    merged_graph: rdflib.Graph,
    ttl_path: Path,
    extension_path: Path,
) -> None:
    """Render a browser-based editor for adding a food-system actor instance."""

    st.title("Food System Actor Builder")
    st.caption(
        "Create a new example actor using the classes and properties "
        "already available in the ontology, or add prototype extension fields."
    )

    st.warning(
        "This editor does not modify the original ontology file. "
        "New content is stored in a separate TTL overlay."
    )

    if st.button("← Back to Ontology Explorer"):
        go_to_view("explorer")

    actor_root = find_actor_root_class(merged_graph)

    if actor_root is None:
        st.error(
            "No FoodSystemActor or GeneralNode root class could be detected in this ontology."
        )
        st.stop()

    allowed_classes = {actor_root} | descendants_of(
        merged_graph,
        actor_root,
    )

    class_options = sorted(
        allowed_classes,
        key=lambda value: english_label(value).casefold(),
    )

    class_lookup = {
        english_label(class_uri): class_uri
        for class_uri in class_options
    }

    st.subheader("1. Node identity")

    left, right = st.columns(2)

    with left:
        node_name = st.text_input(
            "Node name",
            placeholder="Example Farm North",
        )

        node_identifier = st.text_input(
            "Node identifier",
            placeholder="example_farm_north",
            help="Used to generate the new URI.",
        )

    with right:
        default_type_index = 0
        labels = list(class_lookup.keys())

        if "Food System Actor" in labels:
            default_type_index = labels.index("Food System Actor")
        elif "General Node" in labels:
            default_type_index = labels.index("General Node")

        selected_type_label = st.selectbox(
            "Node type",
            options=labels,
            index=default_type_index,
            help=(
                "Choose Food System Actor itself or one of its existing subclasses."
            ),
        )

        selected_class = class_lookup[selected_type_label]

    st.subheader("2. Existing ontology fields")

    datatype_properties = inherited_datatype_properties(
        merged_graph,
        selected_class,
    )

    datatype_lookup = {
        english_label(prop): prop
        for prop in datatype_properties
    }

    selected_field_labels = st.multiselect(
        "Select existing fields",
        options=list(datatype_lookup.keys()),
        default=[
            label
            for label in ["Name", "Latitude", "Longitude", "Node ID"]
            if label in datatype_lookup
        ],
        help=(
            "The list is derived from datatype properties whose domain "
            "matches the selected class or one of its inherited classes."
        ),
    )

    field_values = {}

    if selected_field_labels:
        field_columns = st.columns(2)

        for index, field_label in enumerate(selected_field_labels):
            with field_columns[index % 2]:
                default_value = ""

                if field_label == "Name":
                    default_value = node_name

                field_values[field_label] = st.text_input(
                    field_label,
                    value=default_value,
                    key=f"existing_field_{field_label}",
                )

    st.subheader("3. New prototype fields")

    custom_field_count = st.number_input(
        "Number of additional custom fields",
        min_value=0,
        max_value=10,
        value=0,
        step=1,
    )

    custom_fields = []
    datatype_choices = {
        "Text": XSD.string,
        "Integer": XSD.integer,
        "Decimal": XSD.decimal,
        "Boolean": XSD.boolean,
    }

    for index in range(int(custom_field_count)):
        st.markdown(f"**Custom field {index + 1}**")
        col1, col2, col3 = st.columns([2, 1, 2])

        with col1:
            field_name = st.text_input(
                "Field name",
                key=f"custom_field_name_{index}",
            )

        with col2:
            datatype_label = st.selectbox(
                "Datatype",
                options=list(datatype_choices.keys()),
                key=f"custom_field_type_{index}",
            )

        with col3:
            value = st.text_input(
                "Value",
                key=f"custom_field_value_{index}",
            )

        custom_fields.append(
            {
                "name": field_name,
                "datatype": datatype_choices[datatype_label],
                "value": value,
            }
        )

    st.subheader("4. Existing ontology relations")

    object_properties = inherited_object_properties(
        merged_graph,
        selected_class,
    )

    relation_lookup = {
        english_label(prop): prop
        for prop in object_properties
    }

    if relation_lookup:
        relation_count = st.number_input(
            "Number of existing relations to add",
            min_value=0,
            max_value=10,
            value=0,
            step=1,
        )
    else:
        relation_count = 0
        st.info(
            "No existing object relations are directly available for "
            f"{english_label(selected_class)}. "
            "Choose a more specific actor type (for example Producer, "
            "Food Company, Retail / Food Service, Consumer, or Government) to access "
            "relations defined for those subclasses, or add a new "
            "prototype relation below."
        )

    relations = []

    all_individuals = sorted(
        get_individuals(merged_graph),
        key=lambda value: english_label(value).casefold(),
    )

    for index in range(int(relation_count)):
        st.markdown(f"**Relation {index + 1}**")

        relation_label = st.selectbox(
            "Relation",
            options=list(relation_lookup.keys()),
            key=f"relation_property_{index}",
        )

        if relation_label is None or relation_label not in relation_lookup:
            st.warning(
                "No compatible ontology relation is available for the "
                "selected node type."
            )
            continue

        prop = relation_lookup[relation_label]
        ranges = get_property_ranges(merged_graph, prop)
        target_class = ranges[0] if ranges else None

        compatible_individuals = []

        if target_class is not None:
            compatible_individuals = [
                individual
                for individual in all_individuals
                if (
                    individual,
                    RDF.type,
                    target_class,
                ) in merged_graph
            ]

        target_mode_options = ["Create / reference a new target"]
        if compatible_individuals:
            target_mode_options.insert(0, "Select existing target")

        target_mode = st.radio(
            "Target",
            options=target_mode_options,
            horizontal=True,
            key=f"relation_target_mode_{index}",
        )

        target_uri = None
        target_label = None

        if target_mode == "Select existing target":
            target_lookup = {
                english_label(individual): individual
                for individual in compatible_individuals
            }

            target_label = st.selectbox(
                "Existing target",
                options=list(target_lookup.keys()),
                key=f"relation_target_existing_{index}",
            )
            target_uri = target_lookup[target_label]

        else:
            target_label = st.text_input(
                "New target label",
                placeholder=(
                    english_label(target_class)
                    if target_class is not None
                    else "Target"
                ),
                key=f"relation_target_new_{index}",
            )

        relations.append(
            {
                "property": prop,
                "target_class": target_class,
                "target_uri": target_uri,
                "target_label": target_label,
            }
        )

    st.subheader("5. New prototype relations")

    custom_relation_count = st.number_input(
        "Number of additional custom relations",
        min_value=0,
        max_value=10,
        value=0,
        step=1,
    )

    all_classes = sorted(
        get_classes(merged_graph),
        key=lambda value: english_label(value).casefold(),
    )

    target_class_lookup = {
        english_label(class_uri): class_uri
        for class_uri in all_classes
    }

    custom_relations = []

    for index in range(int(custom_relation_count)):
        st.markdown(f"**Custom relation {index + 1}**")
        col1, col2, col3 = st.columns([2, 2, 2])

        with col1:
            relation_name = st.text_input(
                "Relation name",
                key=f"custom_relation_name_{index}",
            )

        with col2:
            target_class_label = st.selectbox(
                "Target class",
                options=list(target_class_lookup.keys()),
                key=f"custom_relation_target_class_{index}",
            )

        with col3:
            target_label = st.text_input(
                "Target label",
                key=f"custom_relation_target_label_{index}",
            )

        custom_relations.append(
            {
                "name": relation_name,
                "target_class": target_class_lookup[target_class_label],
                "target_label": target_label,
            }
        )

    st.divider()

    save_clicked = st.button(
        "Add node to ontology overlay",
        type="primary",
        use_container_width=True,
    )

    if save_clicked:
        if not node_name.strip():
            st.error("Please provide a node name.")
            st.stop()

        if not node_identifier.strip():
            node_identifier = slugify(node_name)

        namespace = ontology_namespace(base_graph, ttl_path)
        node_uri = namespace[f"node/{slugify(node_identifier)}"]

        extension_graph.bind("user", namespace)
        extension_graph.add((node_uri, RDF.type, OWL.NamedIndividual))
        extension_graph.add((node_uri, RDF.type, selected_class))
        extension_graph.add((node_uri, RDFS.label, Literal(node_name, lang="en")))

        # Existing datatype properties
        for field_label, raw_value in field_values.items():
            if not raw_value.strip():
                continue

            prop = datatype_lookup[field_label]
            ranges = get_property_ranges(merged_graph, prop)
            datatype_uri = ranges[0] if ranges else None

            extension_graph.add(
                (
                    node_uri,
                    prop,
                    parse_literal_value(raw_value, datatype_uri),
                )
            )

        # Custom datatype properties
        for custom_field in custom_fields:
            field_name = custom_field["name"].strip()
            raw_value = custom_field["value"].strip()

            if not field_name or not raw_value:
                continue

            prop = ensure_custom_datatype_property(
                extension_graph,
                namespace,
                field_name,
                selected_class,
                custom_field["datatype"],
            )

            extension_graph.add(
                (
                    node_uri,
                    prop,
                    parse_literal_value(
                        raw_value,
                        custom_field["datatype"],
                    ),
                )
            )

        # Existing object properties
        for relation in relations:
            prop = relation["property"]
            target_uri = relation["target_uri"]
            target_class = relation["target_class"]
            target_label = (relation["target_label"] or "").strip()

            if target_uri is None:
                if not target_label:
                    continue

                target_uri = namespace[
                    f"target/{slugify(target_label)}"
                ]
                extension_graph.add(
                    (
                        target_uri,
                        RDF.type,
                        OWL.NamedIndividual,
                    )
                )

                if target_class is not None:
                    extension_graph.add(
                        (
                            target_uri,
                            RDF.type,
                            target_class,
                        )
                    )

                extension_graph.add(
                    (
                        target_uri,
                        RDFS.label,
                        Literal(target_label, lang="en"),
                    )
                )

            extension_graph.add((node_uri, prop, target_uri))

        # Custom object properties
        for relation in custom_relations:
            relation_name = relation["name"].strip()
            target_label = relation["target_label"].strip()

            if not relation_name or not target_label:
                continue

            target_class = relation["target_class"]
            prop = ensure_custom_object_property(
                extension_graph,
                namespace,
                relation_name,
                selected_class,
                target_class,
            )

            target_uri = namespace[
                f"target/{slugify(target_label)}"
            ]

            extension_graph.add(
                (
                    target_uri,
                    RDF.type,
                    OWL.NamedIndividual,
                )
            )
            extension_graph.add(
                (
                    target_uri,
                    RDF.type,
                    target_class,
                )
            )
            extension_graph.add(
                (
                    target_uri,
                    RDFS.label,
                    Literal(target_label, lang="en"),
                )
            )
            extension_graph.add((node_uri, prop, target_uri))

        save_extension_graph(extension_graph, extension_path)

        st.success(
            f"'{node_name}' was added to the extension overlay."
        )
        st.code(str(extension_path))
        st.info(
            "Return to the explorer tab and press 'Reload user extensions' "
            "to display the new node."
        )


# ============================================================
# Interactive proposal editor
# ============================================================


def render_proposal_editor(
    base_graph: rdflib.Graph,
    instance_graph: rdflib.Graph,
    proposal_graph: rdflib.Graph,
    merged_graph: rdflib.Graph,
    ttl_path: Path,
    proposal_path: Path,
    proposal_state: dict,
) -> None:
    """Render reversible class-level ontology proposal tools."""

    st.title("Ontology Proposal Mode")
    st.caption(
        "Gradually discuss and modify the ontology without changing the base TTL. "
        "Every structural proposal is recorded and can be undone or redone."
    )

    if st.button("← Back to Ontology Explorer"):
        go_to_view("explorer")

    operations = proposal_state.get("operations", [])
    cursor = int(proposal_state.get("cursor", 0))

    st.subheader("Proposal history")
    history_left, history_mid, history_right = st.columns([1, 1, 3])

    with history_left:
        if st.button(
            "↶ Undo",
            disabled=cursor <= 0,
            use_container_width=True,
        ):
            set_proposal_cursor(proposal_path, proposal_state, cursor - 1)
            st.rerun()

    with history_mid:
        if st.button(
            "↷ Redo",
            disabled=cursor >= len(operations),
            use_container_width=True,
        ):
            set_proposal_cursor(proposal_path, proposal_state, cursor + 1)
            st.rerun()

    with history_right:
        history_options = ["0 — Base ontology"]
        for index, operation in enumerate(operations, start=1):
            history_options.append(
                f"{index} — {proposal_operation_description(operation)}"
            )

        selected_history = st.selectbox(
            "Return to a discussion point",
            options=history_options,
            index=cursor,
        )
        selected_cursor = history_options.index(selected_history)

        if selected_cursor != cursor:
            if st.button("Apply selected history point"):
                set_proposal_cursor(
                    proposal_path,
                    proposal_state,
                    selected_cursor,
                )
                st.rerun()

    if operations:
        with st.expander("Show complete change history", expanded=False):
            for index, operation in enumerate(operations, start=1):
                state_marker = "ACTIVE" if index <= cursor else "REDO"
                st.write(
                    f"{index}. [{state_marker}] "
                    f"{proposal_operation_description(operation)}"
                )
    else:
        st.info("No structural proposals have been recorded yet.")

    st.divider()

    actor_root = find_actor_root_class(merged_graph)
    all_classes = get_classes(merged_graph)

    if actor_root is not None:
        preferred_classes = {actor_root} | descendants_of(
            merged_graph,
            actor_root,
        )
    else:
        preferred_classes = all_classes

    class_options = sorted(
        preferred_classes,
        key=lambda value: english_label(value).casefold(),
    )

    class_lookup = {
        f"{english_label(class_uri)}  ·  {local_name(class_uri)}": class_uri
        for class_uri in class_options
    }

    st.subheader("Selected ontology concept")
    st.caption(
        "V5 starts with explicit selection from the graph's food-system actor branch. "
        "Mouse-linked selection can be added later as a UI enhancement; the ontology "
        "operations below already use the same selected concept model."
    )

    selected_option = st.selectbox(
        "Concept",
        options=list(class_lookup.keys()),
    )
    selected_class = class_lookup[selected_option]

    parents = [
        parent
        for child, parent in get_named_subclasses(merged_graph)
        if child == selected_class
    ]

    info_a, info_b = st.columns(2)
    with info_a:
        st.markdown(f"### {english_label(selected_class)}")
        st.code(str(selected_class))
    with info_b:
        st.write("**Parent classes**")
        if parents:
            for parent in parents:
                st.write(f"• {english_label(parent)}")
        else:
            st.write("No named parent class")

    st.divider()
    edit_col, subclass_col = st.columns(2)

    with edit_col:
        st.subheader("Edit proposed label")
        current_label = english_label(selected_class)
        new_label = st.text_input(
            "New English label",
            value=current_label,
            key="proposal_new_label",
        )

        if st.button(
            "Save label proposal",
            use_container_width=True,
            disabled=(not new_label.strip() or new_label.strip() == current_label),
        ):
            operation = {
                "operation": "rename_label",
                "subject": str(selected_class),
                "before": current_label,
                "after": new_label.strip(),
            }
            append_proposal_operation(
                proposal_path,
                proposal_state,
                operation,
            )
            st.rerun()

    with subclass_col:
        st.subheader("Add subclass")
        subclass_label = st.text_input(
            "Subclass label",
            placeholder="Horticultural Producer",
            key="proposal_subclass_label",
        )
        subclass_identifier = st.text_input(
            "Subclass identifier",
            placeholder="horticultural_producer",
            help="Leave blank to derive it from the label.",
            key="proposal_subclass_identifier",
        )

        if st.button(
            "+ Add proposed subclass",
            use_container_width=True,
            disabled=not subclass_label.strip(),
        ):
            identifier = subclass_identifier.strip() or subclass_label.strip()
            child_uri = proposal_class_uri(
                base_graph,
                ttl_path,
                identifier,
            )

            if child_uri == selected_class:
                st.error("The subclass URI would be identical to the selected class.")
            elif child_uri in get_classes(merged_graph):
                st.error(
                    "A class with this generated URI already exists. "
                    "Use another identifier."
                )
            else:
                operation = {
                    "operation": "add_subclass",
                    "parent": str(selected_class),
                    "parent_label": english_label(selected_class),
                    "child": str(child_uri),
                    "label": subclass_label.strip(),
                }
                append_proposal_operation(
                    proposal_path,
                    proposal_state,
                    operation,
                )
                st.rerun()

    st.divider()
    st.subheader("Proposal preview")

    preview_graph = build_ontology_graph(
        rdf_graph=merged_graph,
        complexity=2,
        detailed_mode=False,
        show_attributes=False,
    )

    if selected_class is not None:
        preview_graph = filter_by_focus(
            preview_graph,
            str(selected_class),
            depth=3,
        )

    render_network(build_pyvis(preview_graph))

    st.caption(
        "This preview contains the base ontology plus the active proposal history. "
        "Undo and redo rebuild this state rather than destructively editing the source TTL."
    )

    with st.expander("Proposal files", expanded=False):
        st.write("**Base ontology**")
        st.code(str(ttl_path))
        st.write("**Proposal history**")
        st.code(str(proposal_path))
        st.write(
            "The proposal history is JSON so the discussion sequence remains explicit. "
            "The active RDF proposal layer is reconstructed from it at runtime."
        )


# ============================================================
# V7 direct graph editor
# ============================================================


def _class_ancestor_lookup(graph: rdflib.Graph) -> dict[str, list[str]]:
    """Return each named class together with its named transitive ancestors."""

    parent_map: dict[URIRef, set[URIRef]] = defaultdict(set)
    for child, parent in get_named_subclasses(graph):
        parent_map[child].add(parent)

    result: dict[str, list[str]] = {}
    for class_uri in get_classes(graph):
        ancestors: set[URIRef] = {class_uri}
        frontier = list(parent_map.get(class_uri, set()))
        while frontier:
            parent = frontier.pop()
            if parent in ancestors:
                continue
            ancestors.add(parent)
            frontier.extend(parent_map.get(parent, set()))
        result[str(class_uri)] = sorted(str(value) for value in ancestors)

    return result


def _editor_node_style(category: str, node_type: str) -> dict:
    """Return the V6 visual style used by the direct graph editor."""

    if node_type == "individual":
        return {"shape": "diamond", "size": 19, "color": COLORS["custom_instance"]}
    mapping = {
        "general": ("dot", 42),
        "actor": ("dot", 32),
        "institutional_actor": ("dot", 30),
        "flow": ("hexagon", 32),
        "outcome": ("box", 30),
        "context": ("box", 28),
        "resource": ("dot", 30),
        "datatype": ("box", 17),
        "concept": ("dot", 27),
    }
    shape, size = mapping.get(category, mapping["concept"])
    return {"shape": shape, "size": size, "color": COLORS.get(category, COLORS["concept"])}


def _embedded_vis_javascript() -> str:
    """Extract the in-line vis-network JavaScript bundled by PyVis."""

    probe = Network(height="100px", width="100%", cdn_resources="in_line")
    probe_html = probe.generate_html(notebook=False)
    scripts = re.findall(
        r'<script type="text/javascript">(.*?)</script>',
        probe_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not scripts:
        return ""
    return max(scripts, key=len)


def render_direct_graph_editor(
    base_graph: rdflib.Graph,
    extension_graph: rdflib.Graph,
    merged_graph: rdflib.Graph,
    ttl_path: Path,
    extension_path: Path,
) -> None:
    """Render a mouse-driven graph editor without modifying the source TTL."""

    st.title("Ontology Interactive Graph Editor — V8.1")
    st.caption(
        "Add typed instances directly on the ontology graph, connect them with existing "
        "or prototype relations, and export both a modifications-only TTL and a complete "
        "merged TTL. The original ontology file is never overwritten."
    )
    if st.button("← Back to Ontology Explorer"):
        go_to_view("explorer")
    st.info(
        "Editing is kept in a browser session layer. Export your work with the buttons "
        "inside the editor. Existing extension triples are preserved in the export."
    )

    editor_graph = build_ontology_graph(
        rdf_graph=merged_graph,
        complexity=3,
        detailed_mode=False,
        show_attributes=False,
    )
    add_named_individuals_to_graph(
        schema_graph=editor_graph,
        rdf_graph=merged_graph,
        detailed_mode=False,
        only_user_extensions=False,
        extension_graph=extension_graph,
        show_fields=False,
    )

    # Serialize the currently loaded effective graph as line-oriented RDF.
    # N-Triples statements are also valid Turtle statements and make browser-side
    # deletion/export deterministic without changing the uploaded source file.
    effective_triples = list(merged_graph)
    triple_records = []
    triple_id_by_value = {}

    for index, triple in enumerate(effective_triples):
        subject, predicate, obj = triple
        triple_id = f"triple-{index}"
        line = f"{subject.n3()} {predicate.n3()} {obj.n3()} ."
        triple_records.append({"id": triple_id, "line": line})
        triple_id_by_value[triple] = triple_id

    def deletion_closure_for_node(node_uri: str) -> list[str]:
        """Return triple IDs removed when an original URI node is deleted."""

        uri = URIRef(node_uri)
        selected = {
            triple
            for triple in effective_triples
            if triple[0] == uri or triple[2] == uri
        }

        blank_nodes = {
            value
            for triple in selected
            for value in (triple[0], triple[2])
            if isinstance(value, BNode)
        }

        changed = True
        while changed:
            changed = False
            for triple in effective_triples:
                if triple in selected:
                    continue
                if triple[0] in blank_nodes or triple[2] in blank_nodes:
                    selected.add(triple)
                    for value in (triple[0], triple[2]):
                        if isinstance(value, BNode) and value not in blank_nodes:
                            blank_nodes.add(value)
                            changed = True

        return sorted(
            triple_id_by_value[triple]
            for triple in selected
            if triple in triple_id_by_value
        )

    def deletion_triples_for_visual_edge(
        source: str,
        target: str,
        relation_type: str,
        label: str,
    ) -> list[str]:
        """Map one visible editor edge to the RDF statements that define it."""

        source_uri = URIRef(source)
        target_uri = URIRef(target)
        selected = set()

        if relation_type == "subclass":
            triple = (target_uri, RDFS.subClassOf, source_uri)
            if triple in merged_graph:
                selected.add(triple)

        elif relation_type == "instance":
            triple = (target_uri, RDF.type, source_uri)
            if triple in merged_graph:
                selected.add(triple)

        elif relation_type == "custom_relation":
            for predicate in merged_graph.predicates(source_uri, target_uri):
                if english_label(predicate) == label:
                    selected.add((source_uri, predicate, target_uri))

        elif relation_type == "object_property":
            for prop in get_object_properties(merged_graph):
                if english_label(prop) != label:
                    continue
                domains = set(get_property_domains(merged_graph, prop))
                ranges = set(get_property_ranges(merged_graph, prop))
                if source_uri in domains and target_uri in ranges:
                    domain_triple = (prop, RDFS.domain, source_uri)
                    range_triple = (prop, RDFS.range, target_uri)
                    if domain_triple in merged_graph:
                        selected.add(domain_triple)
                    if range_triple in merged_graph:
                        selected.add(range_triple)

        return sorted(
            triple_id_by_value[triple]
            for triple in selected
            if triple in triple_id_by_value
        )

    node_deletion_map = {
        node_id_value: deletion_closure_for_node(node_id_value)
        for node_id_value in editor_graph.nodes()
        if node_id_value.startswith(("http://", "https://", "urn:"))
    }

    nodes_payload = []
    for node_id_value, data in editor_graph.nodes(data=True):
        node_type = data.get("node_type", "class")
        category = data.get("category", "concept")
        style = _editor_node_style(category, node_type)
        nodes_payload.append(
            {
                "id": node_id_value,
                "label": data.get("label", node_id_value),
                "title": data.get("title", ""),
                "shape": style["shape"],
                "size": style["size"],
                "color": style["color"],
                "nodeType": node_type,
                "category": category,
                "isNew": False,
            }
        )

    edges_payload = []
    for index, (source, target, key, data) in enumerate(
        editor_graph.edges(keys=True, data=True)
    ):
        relation_type = data.get("relation_type", "object_property")
        edges_payload.append(
            {
                "id": f"base-edge-{index}",
                "from": source,
                "to": target,
                "label": data.get("label", ""),
                "title": data.get("title", ""),
                "arrows": "to",
                "color": EDGE_COLORS.get(relation_type, "#64748B"),
                "dashes": relation_type in {"datatype_property", "restriction", "instance"},
                "isNew": False,
                "relationType": relation_type,
                "deletionTripleIds": deletion_triples_for_visual_edge(
                    source=source,
                    target=target,
                    relation_type=relation_type,
                    label=data.get("label", ""),
                ),
            }
        )

    classes = sorted(
        get_classes(merged_graph),
        key=lambda value: english_label(value).casefold(),
    )
    class_payload = []
    for class_uri in classes:
        category = class_category(merged_graph, class_uri)
        style = _editor_node_style(category, "class")
        fields = []
        for prop in inherited_datatype_properties(merged_graph, class_uri):
            ranges = get_property_ranges(merged_graph, prop)
            fields.append(
                {
                    "uri": str(prop),
                    "label": english_label(prop),
                    "datatype": str(ranges[0]) if ranges else str(XSD.string),
                }
            )
        class_payload.append(
            {
                "uri": str(class_uri),
                "label": english_label(class_uri),
                "category": category,
                "shape": style["shape"],
                "size": style["size"],
                "color": style["color"],
                "fields": fields,
            }
        )

    relation_payload = []
    for prop in sorted(
        get_object_properties(merged_graph),
        key=lambda value: english_label(value).casefold(),
    ):
        relation_payload.append(
            {
                "uri": str(prop),
                "label": english_label(prop),
                "domains": [str(value) for value in get_property_domains(merged_graph, prop)],
                "ranges": [str(value) for value in get_property_ranges(merged_graph, prop)],
            }
        )

    class_set = get_classes(merged_graph)
    individual_class_map: dict[str, str] = {}
    for individual in get_individuals(merged_graph):
        for value in merged_graph.objects(individual, RDF.type):
            if isinstance(value, URIRef) and value in class_set:
                individual_class_map[str(individual)] = str(value)
                break

    base_text = ttl_path.read_text(encoding="utf-8")
    existing_extension_text = (
        extension_graph.serialize(format="turtle") if len(extension_graph) else ""
    )
    namespace = ontology_namespace(base_graph, ttl_path)

    payload = {
        "nodes": nodes_payload,
        "edges": edges_payload,
        "classes": class_payload,
        "relations": relation_payload,
        "ancestors": _class_ancestor_lookup(merged_graph),
        "individualClasses": individual_class_map,
        "userNamespace": str(namespace),
        "baseTtl": base_text,
        "existingExtensionTtl": existing_extension_text,
        "sourceName": ttl_path.name,
        "extensionName": extension_path.name,
        "effectiveTriples": triple_records,
        "nodeDeletionMap": node_deletion_map,
    }
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    # Build the editor on top of the same PyVis HTML used by the explorer.
    # This guarantees that the original ontology graph is preloaded before
    # the mouse-editing controls are attached.
    editor_network = build_pyvis(editor_graph, height_px=900)
    editor_html = editor_network.generate_html(notebook=False)

    editor_css = r"""
<style>
html, body { margin: 0 !important; padding: 0 !important; overflow: hidden !important; background: #F8FAFC !important; }
#mynetwork {
  width: calc(100% - 390px) !important;
  height: 920px !important;
  border: 0 !important;
  background: #F8FAFC !important;
}
#v71-panel {
  position: fixed;
  top: 0;
  right: 0;
  width: 390px;
  height: 100vh;
  overflow-y: auto;
  box-sizing: border-box;
  padding: 16px;
  border-left: 1px solid #CBD5E1;
  background: #FFFFFF;
  color: #0F172A;
  font-family: Arial, sans-serif;
  z-index: 100000;
}
#v71-toolbar {
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 99999;
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  padding: 7px;
  border: 1px solid #CBD5E1;
  border-radius: 10px;
  background: rgba(255,255,255,.96);
  box-shadow: 0 4px 14px rgba(15,23,42,.12);
}
#v71-toolbar button, #v71-panel button {
  border: 1px solid #CBD5E1;
  border-radius: 7px;
  background: #FFFFFF;
  color: #0F172A;
  padding: 8px 10px;
  cursor: pointer;
  font-weight: 600;
}
#v71-toolbar button.active { background: #0F172A; color: white; border-color: #0F172A; }
#v71-panel button.primary { background: #2563EB; color: white; border-color: #2563EB; }
#v71-panel button.danger { background: #FFF1F2; color: #BE123C; border-color: #FECDD3; }
#v71-panel input, #v71-panel select, #v71-panel textarea {
  width: 100%; box-sizing: border-box; padding: 8px 9px; border: 1px solid #CBD5E1;
  border-radius: 7px; background: white; color: #0F172A;
}
#v71-panel label { display: block; font-size: 12px; font-weight: 700; margin: 9px 0 5px; }
.v71-title { font-size: 17px; font-weight: 700; margin-bottom: 4px; }
.v71-muted { color: #64748B; font-size: 12px; line-height: 1.4; }
.v71-section { border-top: 1px solid #E2E8F0; margin-top: 16px; padding-top: 14px; }
.v71-box { border: 1px solid #CBD5E1; background: #F8FAFC; border-radius: 8px; padding: 8px; font-size: 12px; word-break: break-word; }
.v71-field { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px; margin-top: 7px; }
#v71-status {
  position: fixed; left: 12px; bottom: 12px; z-index: 99999; max-width: calc(100% - 430px);
  background: rgba(15,23,42,.90); color: white; padding: 8px 10px; border-radius: 8px;
  font-family: Arial, sans-serif; font-size: 12px;
}
:fullscreen #mynetwork { width: calc(100vw - 390px) !important; height: 100vh !important; }
:fullscreen #v71-panel { height: 100vh !important; }
</style>
"""

    editor_panel = r"""
<div id="v71-toolbar">
  <button id="v71-select" class="active">Select</button>
  <button id="v71-add">Add node</button>
  <button id="v71-connect">Connect</button>
  <button id="v71-delete">Delete</button>
  <button id="v71-fit">Fit</button>
  <button id="v71-fullscreen">Fullscreen</button>
</div>
<div id="v71-status">Select mode: inspect and move nodes.</div>
<aside id="v71-panel">
  <div class="v71-title">Interactive Graph Editor</div>
  <div class="v71-muted">The uploaded ontology is preloaded as a working copy. Nodes and connections can be added or removed here without changing the uploaded source file.</div>

  <div class="v71-section">
    <div class="v71-title" style="font-size:14px">Add typed node</div>
    <label>Node type</label>
    <select id="v71-node-type"></select>
    <div id="v71-type-preview" class="v71-box" style="margin-top:8px"></div>
    <label>Name</label>
    <input id="v71-node-name" placeholder="Example Farm North" />
    <label>Identifier</label>
    <input id="v71-node-id" placeholder="example_farm_north" />

    <label>Initial graph connection</label>
    <select id="v71-initial-link"></select>
    <div class="v71-muted">
      The node is always typed with rdf:type. This option controls the visible/semantic
      connection created from the selected class to the new node.
    </div>
    <label>Custom initial relation (optional)</label>
    <input id="v71-initial-custom" placeholder="e.g. has prototype member" />

    <div id="v71-fields"></div>
    <button id="v71-arm-add" class="primary" style="width:100%;margin-top:10px">Ready — click graph to place</button>
  </div>

  <div class="v71-section">
    <div class="v71-title" style="font-size:14px">Connect nodes</div>
    <div class="v71-muted">Click a source node, then a target node. Class→class creates an OWL restriction; instance→instance creates an RDF assertion.</div>
    <label>Source</label><div id="v71-source" class="v71-box">Not selected</div>
    <label>Target</label><div id="v71-target" class="v71-box">Not selected</div>
    <label>Ontology relation</label><select id="v71-relation"></select>
    <label><input id="v71-show-all" type="checkbox" style="width:auto;margin-right:5px" /> Show all ontology relations</label>
    <div class="v71-muted" style="text-align:center;margin:8px 0">or</div>
    <label>New prototype relation</label><input id="v71-custom-relation" placeholder="supplies to" />
    <button id="v71-create-edge" class="primary" style="width:100%;margin-top:10px">Create connection</button>
  </div>

  <div class="v71-section">
    <div class="v71-title" style="font-size:14px">Selection</div>
    <div id="v71-selection" class="v71-box">Nothing selected</div>
  </div>

  <div class="v71-section">
    <div class="v71-title" style="font-size:14px">Export</div>
    <div class="v71-muted"><span id="v71-change-count">0</span> session changes.</div>
    <button id="v71-download-mods" class="primary" style="width:100%;margin-top:10px">Download modifications TTL</button>
    <button id="v71-download-merged" style="width:100%;margin-top:8px">Download complete merged TTL</button>
    <button id="v71-reset" class="danger" style="width:100%;margin-top:8px">Reset session changes</button>
  </div>
</aside>
"""

    editor_js = r"""
<script>
(function () {
  const PAYLOAD = __PAYLOAD__;
  const XSD = "http://www.w3.org/2001/XMLSchema#";
  const RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type";
  const RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label";
  const RDFS_DOMAIN = "http://www.w3.org/2000/01/rdf-schema#domain";
  const RDFS_RANGE = "http://www.w3.org/2000/01/rdf-schema#range";
  const RDFS_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf";
  const OWL_NAMED_INDIVIDUAL = "http://www.w3.org/2002/07/owl#NamedIndividual";
  const OWL_OBJECT_PROPERTY = "http://www.w3.org/2002/07/owl#ObjectProperty";
  const OWL_RESTRICTION = "http://www.w3.org/2002/07/owl#Restriction";
  const OWL_ON_PROPERTY = "http://www.w3.org/2002/07/owl#onProperty";
  const OWL_SOME_VALUES_FROM = "http://www.w3.org/2002/07/owl#someValuesFrom";
  const OWL_HAS_VALUE = "http://www.w3.org/2002/07/owl#hasValue";

  if (typeof network === "undefined" || typeof nodes === "undefined" || typeof edges === "undefined") {
    document.getElementById("v71-status").textContent = "Editor initialization failed: PyVis graph objects were not found.";
    return;
  }

  const storageKey = "ontology-v81-editor::" + PAYLOAD.sourceName;
  let state = {nodes: [], edges: [], deletedNodes: [], deletedEdges: []};
  let mode = "select";
  let addArmed = false;
  let sourceId = null;
  let targetId = null;

  const baseMeta = new Map(PAYLOAD.nodes.map(n => [n.id, n]));
  const classMap = new Map(PAYLOAD.classes.map(c => [c.uri, c]));

  function esc(v) { return String(v).replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/\n/g,"\\n").replace(/\r/g,"\\r"); }
  function slug(v) { return String(v || "node").toLowerCase().trim().replace(/[^a-z0-9]+/g,"_").replace(/^_+|_+$/g,"") || "node"; }
  function iri(v) { return `<${v}>`; }
  function lit(v,d) { const t=esc(v); return (!d || d===XSD+"string") ? `"${t}"` : `"${t}"^^<${d}>`; }
  function status(t) { document.getElementById("v71-status").textContent=t; }
  function count() { document.getElementById("v71-change-count").textContent=state.nodes.length+state.edges.length+(state.deletedNodes||[]).length+(state.deletedEdges||[]).length; }
  function save() { localStorage.setItem(storageKey, JSON.stringify(state)); count(); }
  function cls(uri) { return classMap.get(uri) || null; }
  function ancestry(uri) { return PAYLOAD.ancestors[uri] || (uri ? [uri] : []); }
  function nodeMeta(id) { return nodes.get(id) || baseMeta.get(id) || null; }
  function nodeClass(id) {
    const n = nodes.get(id);
    if (n && n.classUri) return n.classUri;
    if (PAYLOAD.individualClasses[id]) return PAYLOAD.individualClasses[id];
    if (classMap.has(id)) return id;
    return null;
  }
  function isIndividual(id) {
    const n = nodes.get(id);
    const m = baseMeta.get(id);
    return Boolean((n && n.nodeType === "individual") || (m && m.nodeType === "individual") || PAYLOAD.individualClasses[id]);
  }
  function isClass(id) { return classMap.has(id); }
  function endpointKind(id) {
    if (isIndividual(id)) return "individual";
    if (isClass(id)) return "class";
    return "other";
  }
  function endpointLabel(id) {
    const n = nodes.get(id);
    const m = baseMeta.get(id);
    return (n && n.label) || (m && m.label) || id || "Not selected";
  }
  function compatible(p,s,t) {
    const sa=ancestry(s), ta=ancestry(t);
    return (!p.domains.length || p.domains.some(v=>sa.includes(v))) && (!p.ranges.length || p.ranges.some(v=>ta.includes(v)));
  }

  function setMode(next) {
    mode=next; addArmed=false;
    [["select","v71-select"],["add","v71-add"],["connect","v71-connect"],["delete","v71-delete"]].forEach(([m,id])=>document.getElementById(id).classList.toggle("active",m===mode));
    if (mode==="select") status("Select mode: inspect and move nodes.");
    if (mode==="add") status("Add node mode: complete the form, arm placement, then click empty graph space.");
    if (mode==="connect") status("Connect mode: click source node, then target node. Use class→class or instance→instance.");
    if (mode==="delete") status("Delete mode: click any node or connection. Original RDF stays untouched; deletions affect only the exported change set and merged TTL.");
  }

  const typeSelect=document.getElementById("v71-node-type");
  const initialLinkSelect=document.getElementById("v71-initial-link");

  [
    {value:"instance", label:"Instance of (rdf:type) — dashed type link"},
    {value:"none", label:"No visible class link"},
  ].forEach(item=>{
    const o=document.createElement("option");
    o.value=item.value;
    o.textContent=item.label;
    initialLinkSelect.appendChild(o);
  });

  PAYLOAD.relations.forEach(r=>{
    const o=document.createElement("option");
    o.value="relation::"+r.uri;
    o.textContent=`Ontology relation: ${r.label}`;
    initialLinkSelect.appendChild(o);
  });

  PAYLOAD.classes.forEach(c=>{
    const o=document.createElement("option");
    o.value=c.uri;
    o.textContent=`${c.label} — ${c.category.replaceAll("_"," ")}`;
    typeSelect.appendChild(o);
  });

  function renderFields() {
    const c=cls(typeSelect.value);
    const fields=document.getElementById("v71-fields");
    const preview=document.getElementById("v71-type-preview");
    fields.innerHTML="";
    if (!c) { preview.textContent="No ontology classes were loaded."; return; }
    preview.innerHTML=`<b>${c.label}</b><br><span style="color:${c.color}">●</span> ${c.category.replaceAll("_"," ")} · ${c.shape}`;
    c.fields.forEach((f,i)=>{
      const row=document.createElement("div"); row.className="v71-field";
      row.innerHTML=`<label>${f.label}</label><input data-field-uri="${f.uri}" data-field-datatype="${f.datatype}" data-field-label="${f.label}" id="v71-field-${i}" />`;
      fields.appendChild(row);
    });
  }
  typeSelect.addEventListener("change",renderFields); renderFields();

  document.getElementById("v71-node-name").addEventListener("input",e=>{
    const id=document.getElementById("v71-node-id"); if(!id.dataset.touched) id.value=slug(e.target.value);
  });
  document.getElementById("v71-node-id").addEventListener("input",e=>e.target.dataset.touched="1");

  document.getElementById("v71-select").onclick=()=>setMode("select");
  document.getElementById("v71-add").onclick=()=>setMode("add");
  document.getElementById("v71-connect").onclick=()=>{setMode("connect");sourceId=null;targetId=null;refreshRelations();};
  document.getElementById("v71-delete").onclick=()=>setMode("delete");
  document.getElementById("v71-fit").onclick=()=>network.fit({animation:false});
  document.getElementById("v71-fullscreen").onclick=async()=>{if(!document.fullscreenElement)await document.documentElement.requestFullscreen();else await document.exitFullscreen();};
  document.getElementById("v71-arm-add").onclick=()=>{
    if(!document.getElementById("v71-node-name").value.trim()){alert("Enter a node name first.");return;}
    setMode("add"); addArmed=true; status("Placement armed: click empty graph space to create the node.");
  };

  function fieldValues(){
    const result=[];
    document.querySelectorAll("#v71-fields input[data-field-uri]").forEach(i=>{
      const v=i.value.trim(); if(v) result.push({uri:i.dataset.fieldUri,datatype:i.dataset.fieldDatatype,label:i.dataset.fieldLabel,value:v});
    });
    return result;
  }

  function addNode(x,y){
    const c=cls(typeSelect.value);
    const name=document.getElementById("v71-node-name").value.trim();
    const identifier=slug(document.getElementById("v71-node-id").value.trim()||name);
    if(!c||!name)return;

    let uri=PAYLOAD.userNamespace+"instance/"+identifier;
    if(nodes.get(uri))uri=PAYLOAD.userNamespace+"instance/"+identifier+"_"+Date.now();

    const fields=fieldValues();
    const initialChoice=initialLinkSelect.value || "instance";
    const initialCustom=document.getElementById("v71-initial-custom").value.trim();

    nodes.add({
      id:uri,label:name,title:`${name}\n\nUser-added instance of ${c.label}`,
      shape:c.shape,size:Math.max(20,c.size-2),color:c.color,
      nodeType:"individual",category:"custom_instance",classUri:c.uri,
      isNew:true,x,y,fixed:false
    });

    const nodeRecord={
      id:uri,label:name,classUri:c.uri,fields,x,y,
      initialLinkType:initialChoice,
      initialCustomRelation:initialCustom,
      instanceEdgeId:null
    };

    if(initialCustom){
      const relationUri=PAYLOAD.userNamespace+"relation/"+slug(initialCustom);
      const edgeId="new-initial-edge-"+Date.now();
      edges.add({
        id:edgeId,from:c.uri,to:uri,label:initialCustom,arrows:"to",
        dashes:false,color:"#DB2777",width:2.2,isNew:true,semantic:true
      });
      state.edges.push({
        id:edgeId,source:c.uri,target:uri,relationUri,
        relationLabel:initialCustom,custom:true,edgeKind:"class_to_instance",
        sourceClass:c.uri,targetClass:c.uri,initial:true
      });
      nodeRecord.instanceEdgeId=edgeId;
    } else if(initialChoice==="instance"){
      const edgeId="new-instance-edge-"+Date.now();
      edges.add({
        id:edgeId,from:c.uri,to:uri,label:"instance",arrows:"to",
        dashes:true,color:"#B45309",isNew:true,semantic:false
      });
      nodeRecord.instanceEdgeId=edgeId;
    } else if(initialChoice.startsWith("relation::")){
      const relationUri=initialChoice.slice("relation::".length);
      const relation=PAYLOAD.relations.find(r=>r.uri===relationUri);
      const relationLabel=relation?.label || "relation";
      const edgeId="new-initial-edge-"+Date.now();
      edges.add({
        id:edgeId,from:c.uri,to:uri,label:relationLabel,arrows:"to",
        dashes:false,color:"#DB2777",width:2.2,isNew:true,semantic:true
      });
      state.edges.push({
        id:edgeId,source:c.uri,target:uri,relationUri,
        relationLabel,custom:false,edgeKind:"class_to_instance",
        sourceClass:c.uri,targetClass:c.uri,initial:true
      });
      nodeRecord.instanceEdgeId=edgeId;
    }

    state.nodes.push(nodeRecord);
    save();

    document.getElementById("v71-node-name").value="";
    document.getElementById("v71-node-id").value="";
    document.getElementById("v71-initial-custom").value="";
    delete document.getElementById("v71-node-id").dataset.touched;
    document.querySelectorAll("#v71-fields input").forEach(i=>i.value="");
    addArmed=false;
    status(`Added ${name}.`);
  }

  function selectionInfo(id){
    const box=document.getElementById("v71-selection");
    if(!id){box.textContent="Nothing selected";return;}
    const n=nodes.get(id); const m=baseMeta.get(id); const c=cls(nodeClass(id));
    box.innerHTML=`<b>${(n&&n.label)||(m&&m.label)||id}</b><br>${isIndividual(id)?"instance":classMap.has(id)?"ontology class":"node"}${c?`<br>Type: ${c.label}`:""}<br><span style="color:#64748B">${n&&n.isNew?"Session-created":"Original / loaded graph"}</span>`;
  }

  function refreshRelations(){
    const s=sourceId?nodeMeta(sourceId):null, t=targetId?nodeMeta(targetId):null;
    document.getElementById("v71-source").textContent=sourceId?`${endpointLabel(sourceId)} (${endpointKind(sourceId)})`:"Not selected";
    document.getElementById("v71-target").textContent=targetId?`${endpointLabel(targetId)} (${endpointKind(targetId)})`:"Not selected";
    const sel=document.getElementById("v71-relation"); sel.innerHTML="";
    if(!s||!t){const o=document.createElement("option");o.textContent="Select source and target first";sel.appendChild(o);return;}
    const sk=endpointKind(sourceId), tk=endpointKind(targetId);
    if(!["class","individual"].includes(sk) || !["class","individual"].includes(tk)){const o=document.createElement("option");o.textContent="Select ontology classes or instance nodes";sel.appendChild(o);return;}
    const showAll=document.getElementById("v71-show-all").checked;
    const options=PAYLOAD.relations.filter(p=>showAll||compatible(p,nodeClass(sourceId),nodeClass(targetId)));
    if(!options.length){const o=document.createElement("option");o.textContent="No compatible ontology relation";sel.appendChild(o);return;}
    options.forEach(p=>{const o=document.createElement("option");o.value=p.uri;o.textContent=p.label;sel.appendChild(o);});
  }
  document.getElementById("v71-show-all").onchange=refreshRelations;

  document.getElementById("v71-create-edge").onclick=()=>{
    if(!sourceId||!targetId){alert("Select a source and target node first.");return;}
    const sourceKind=endpointKind(sourceId), targetKind=endpointKind(targetId);
    if(!["class","individual"].includes(sourceKind) || !["class","individual"].includes(targetKind)){
      alert("Select ontology classes or instance nodes as both endpoints.");
      return;
    }
    const custom=document.getElementById("v71-custom-relation").value.trim();
    const sel=document.getElementById("v71-relation");
    const uri=custom?PAYLOAD.userNamespace+"relation/"+slug(custom):sel.value;
    const label=custom||(PAYLOAD.relations.find(r=>r.uri===uri)?.label||"relation");
    if(!uri){alert("Choose an ontology relation or enter a prototype relation.");return;}
    const id="new-edge-"+Date.now();
    edges.add({id,from:sourceId,to:targetId,label,arrows:"to",color:"#DB2777",width:2.2,isNew:true,semantic:true});
    let edgeKind="assertion";
    if(sourceKind==="class" && targetKind==="class") edgeKind="schema";
    else if(sourceKind==="individual" && targetKind==="class") edgeKind="instance_to_class";
    else if(sourceKind==="class" && targetKind==="individual") edgeKind="class_to_instance";
    state.edges.push({id,source:sourceId,target:targetId,relationUri:uri,relationLabel:label,custom:Boolean(custom),edgeKind,sourceClass:nodeClass(sourceId),targetClass:nodeClass(targetId)}); save();
    document.getElementById("v71-custom-relation").value=""; sourceId=null;targetId=null;refreshRelations();status(`Created relation: ${label}.`);
  };

  network.on("click",p=>{
    if(mode==="add"&&addArmed&&!p.nodes.length&&!p.edges.length){addNode(p.pointer.canvas.x,p.pointer.canvas.y);return;}
    if(mode==="delete"){
      if(p.nodes.length){
        const id=p.nodes[0], n=nodes.get(id);

        if(n?.isNew){
          edges.get({filter:e=>e.from===id||e.to===id}).forEach(e=>edges.remove(e.id));
          nodes.remove(id);
          state.nodes=state.nodes.filter(x=>x.id!==id);
          state.edges=state.edges.filter(e=>e.source!==id&&e.target!==id);
          save();
          status("Session-created node deleted.");
          return;
        }

        const originalLabel=endpointLabel(id);
        if(!confirm(`Remove "${originalLabel}" from the modified/merged ontology? The uploaded source file will not be changed.`)) return;

        const tripleIds=(PAYLOAD.nodeDeletionMap[id]||[]).slice();
        const incidentBaseEdges=edges.get({
          filter:e=>(e.from===id||e.to===id) && !e.isNew
        });

        incidentBaseEdges.forEach(e=>{
          if(!(state.deletedEdges||[]).some(x=>x.id===e.id)){
            state.deletedEdges.push({
              id:e.id,label:e.label||"",source:e.from,target:e.to,
              tripleIds:(e.deletionTripleIds||[]).slice()
            });
          }
          edges.remove(e.id);
        });

        if(!(state.deletedNodes||[]).some(x=>x.id===id)){
          state.deletedNodes.push({id,label:originalLabel,tripleIds});
        }

        edges.get({filter:e=>e.from===id||e.to===id}).forEach(e=>edges.remove(e.id));
        nodes.remove(id);
        save();
        selectionInfo(null);
        status(`Removed ${originalLabel} from the working graph. The source TTL remains unchanged.`);
        return;
      }

      if(p.edges.length){
        const id=p.edges[0], e=edges.get(id);
        if(!e)return;

        if(e.isNew){
          edges.remove(id);
          state.edges=state.edges.filter(x=>x.id!==id);
          save();
          status("Session-created relation deleted.");
          return;
        }

        if(!confirm(`Remove connection "${e.label||"relation"}" from the modified/merged ontology? The uploaded source file will not be changed.`)) return;

        if(!(state.deletedEdges||[]).some(x=>x.id===id)){
          state.deletedEdges.push({
            id,label:e.label||"",source:e.from,target:e.to,
            tripleIds:(e.deletionTripleIds||[]).slice()
          });
        }

        edges.remove(id);
        save();
        status("Original connection removed from the working graph.");
        return;
      }
    }
    if(p.nodes.length){
      const id=p.nodes[0]; selectionInfo(id);
      if(mode==="connect"){
        const kind=endpointKind(id);
        if(!["class","individual"].includes(kind)){status("Connect mode accepts ontology classes and instance nodes.");return;}
        if(!sourceId){
          sourceId=id; targetId=null;
          status(`Source selected: ${endpointLabel(id)} (${kind}). Now select a target node.`);
        } else if(!targetId && id!==sourceId){
          targetId=id;
          status(`Target selected: ${endpointLabel(id)} (${kind}). Choose a relation and create the connection.`);
        } else if(id===sourceId){
          sourceId=null; targetId=null; status("Source cleared. Select a new source node.");
        } else {
          sourceId=id; targetId=null; status(`Source reset to: ${endpointLabel(id)} (${kind}). Now select a target node.`);
        }
        refreshRelations();
      }
    } else selectionInfo(null);
  });

  network.on("dragEnd",p=>{
    if(!p.nodes?.length)return;
    p.nodes.forEach(id=>{const n=nodes.get(id);if(!n?.isNew)return;const pos=network.getPositions([id])[id],s=state.nodes.find(x=>x.id===id);if(s){s.x=pos.x;s.y=pos.y;}}); save();
  });

  function nodeTTL(n){let ttl=`${iri(n.id)} ${iri(RDF_TYPE)} ${iri(OWL_NAMED_INDIVIDUAL)} , ${iri(n.classUri)} ;\n  ${iri(RDFS_LABEL)} \"${esc(n.label)}\"@en`; (n.fields||[]).forEach(f=>ttl+=` ;\n  ${iri(f.uri)} ${lit(f.value,f.datatype)}`); return ttl+" .\n\n";}
  function edgeTTL(e){
    let ttl="";
    const kind=e.edgeKind || (isClass(e.source)&&isClass(e.target)?"schema":"assertion");
    if(e.custom){
      ttl+=`${iri(e.relationUri)} ${iri(RDF_TYPE)} ${iri(OWL_OBJECT_PROPERTY)} ;\n  ${iri(RDFS_LABEL)} \"${esc(e.relationLabel)}\"@en`;
      if(e.sourceClass) ttl+=` ;\n  ${iri(RDFS_DOMAIN)} ${iri(e.sourceClass)}`;
      if(e.targetClass) ttl+=` ;\n  ${iri(RDFS_RANGE)} ${iri(e.targetClass)}`;
      ttl+=" .\n\n";
    }
    if(kind==="schema"){
      ttl+=`${iri(e.source)} ${iri(RDFS_SUBCLASS)} [\n  ${iri(RDF_TYPE)} ${iri(OWL_RESTRICTION)} ;\n  ${iri(OWL_ON_PROPERTY)} ${iri(e.relationUri)} ;\n  ${iri(OWL_SOME_VALUES_FROM)} ${iri(e.target)}\n] .\n\n`;
    } else if(kind==="instance_to_class"){
      ttl+=`${iri(e.source)} ${iri(e.relationUri)} [\n  ${iri(RDF_TYPE)} ${iri(e.target)}\n] .\n\n`;
    } else if(kind==="class_to_instance"){
      ttl+=`${iri(e.source)} ${iri(RDFS_SUBCLASS)} [\n  ${iri(RDF_TYPE)} ${iri(OWL_RESTRICTION)} ;\n  ${iri(OWL_ON_PROPERTY)} ${iri(e.relationUri)} ;\n  ${iri(OWL_HAS_VALUE)} ${iri(e.target)}\n] .\n\n`;
    } else {
      ttl+=`${iri(e.source)} ${iri(e.relationUri)} ${iri(e.target)} .\n\n`;
    }
    return ttl;
  }
  function deletedTripleIds(){
    const ids=new Set();
    (state.deletedNodes||[]).forEach(item=>(item.tripleIds||[]).forEach(id=>ids.add(id)));
    (state.deletedEdges||[]).forEach(item=>(item.tripleIds||[]).forEach(id=>ids.add(id)));
    return ids;
  }

  function deletionManifestTTL(){
    const deleted=deletedTripleIds();
    if(!deleted.size)return "";

    let ttl="# --- Deletions from uploaded ontology ---\n";
    ttl+="# These records describe removals. The complete merged TTL applies them physically.\n";
    ttl+="@prefix oechange: <urn:ontology-explorer:v8:change#> .\n\n";

    const byId=new Map((PAYLOAD.effectiveTriples||[]).map(t=>[t.id,t.line]));
    [...deleted].sort().forEach(id=>{
      const line=byId.get(id);
      if(!line)return;
      ttl+=`[] a oechange:Deletion ; oechange:triple "${esc(line)}" .\n`;
    });
    return ttl+"\n";
  }

  function additionsTTL(){
    let ttl="# --- V8.1 additions ---\n\n";
    state.nodes.forEach(n=>ttl+=nodeTTL(n));
    state.edges.forEach(e=>ttl+=edgeTTL(e));
    return ttl;
  }

  function sessionTTL(){
    return "# V8.1 interactive graph editor session modifications\n"
      + "# The uploaded source ontology was not modified.\n\n"
      + deletionManifestTTL()
      + additionsTTL();
  }

  function modsTTL(){
    let e=PAYLOAD.existingExtensionTtl||"";
    if(e&&!e.endsWith("\n"))e+="\n";
    return e+(e?"\n":"")+sessionTTL();
  }

  function mergedTTL(){
    const deleted=deletedTripleIds();
    const remaining=(PAYLOAD.effectiveTriples||[])
      .filter(t=>!deleted.has(t.id))
      .map(t=>t.line)
      .join("\n");

    return "# Ontology Explorer V8.1 — complete merged ontology\n"
      + "# Uploaded source kept unchanged; deletions and additions are applied here.\n\n"
      + remaining
      + "\n\n"
      + additionsTTL();
  }
  function download(name,text){const blob=new Blob([text],{type:"text/turtle;charset=utf-8"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),500);}
  document.getElementById("v71-download-mods").onclick=()=>download(PAYLOAD.sourceName.replace(/\.ttl$/i,"")+"_modifications.ttl",modsTTL());
  document.getElementById("v71-download-merged").onclick=()=>download(PAYLOAD.sourceName.replace(/\.ttl$/i,"")+"_merged.ttl",mergedTTL());
  document.getElementById("v71-reset").onclick=()=>{if(confirm("Clear all V8 session changes from this browser?")){localStorage.removeItem(storageKey);location.reload();}};

  function restore(){
    try{
      const saved=JSON.parse(localStorage.getItem(storageKey)||"null");
      if(saved)state=saved;
    }catch(e){
      state={nodes:[],edges:[],deletedNodes:[],deletedEdges:[]};
    }

    state.nodes=state.nodes||[];
    state.edges=state.edges||[];
    state.deletedNodes=state.deletedNodes||[];
    state.deletedEdges=state.deletedEdges||[];

    state.nodes.forEach(n=>{
      const c=cls(n.classUri);
      if(!c||nodes.get(n.id))return;

      nodes.add({
        id:n.id,label:n.label,title:`${n.label}\n\nUser-added instance of ${c.label}`,
        shape:c.shape,size:Math.max(20,c.size-2),color:c.color,
        nodeType:"individual",category:"custom_instance",classUri:n.classUri,
        isNew:true,x:n.x,y:n.y,fixed:false
      });

      const hasSemanticInitial=state.edges.some(e=>e.id===n.instanceEdgeId);
      if(n.initialLinkType==="instance" && n.instanceEdgeId && !hasSemanticInitial){
        edges.add({
          id:n.instanceEdgeId,from:n.classUri,to:n.id,label:"instance",arrows:"to",
          dashes:true,color:"#B45309",isNew:true,semantic:false
        });
      }
    });

    state.edges.forEach(e=>{
      if(!edges.get(e.id)){
        edges.add({
          id:e.id,from:e.source,to:e.target,label:e.relationLabel,arrows:"to",
          color:"#DB2777",width:2.2,isNew:true,semantic:true
        });
      }
    });

    state.deletedEdges.forEach(e=>{
      if(edges.get(e.id))edges.remove(e.id);
    });

    state.deletedNodes.forEach(n=>{
      edges.get({filter:e=>e.from===n.id||e.to===n.id}).forEach(e=>edges.remove(e.id));
      if(nodes.get(n.id))nodes.remove(n.id);
    });
  }

  restore(); count(); refreshRelations();
  setTimeout(()=>{network.fit({animation:false});network.redraw();},350);
})();
</script>
""".replace("__PAYLOAD__", payload_json)

    editor_html = editor_html.replace("</head>", editor_css + "\n</head>")
    editor_html = editor_html.replace("</body>", editor_panel + "\n" + editor_js + "\n</body>")

    components.html(editor_html, height=950, scrolling=False)
    st.caption(
        "The editor starts from the same preloaded ontology graph as the explorer. "
        "The modifications TTL contains only the extension layer and V8 additions; "
        "the merged TTL contains the untouched source ontology plus those additions."
    )


# ============================================================
# Explorer page
# ============================================================


def render_explorer(
    base_graph: rdflib.Graph,
    extension_graph: rdflib.Graph,
    rdf_graph: rdflib.Graph,
    ttl_path: Path,
    extension_path: Path,
) -> None:
    """Render the main ontology explorer."""

    st.title("Ontology Explorer V8.1 — Cloud")
    st.caption(
        "Upload and explore your own OWL/RDF ontology in Turtle format. "
        "No ontology is bundled or opened by default."
    )

    if st.button(
        "✦ Open Interactive Graph Editor",
        use_container_width=False,
    ):
        go_to_view("editor")

    with st.sidebar:
        st.header("Project")
        st.caption("Uploaded ontology")
        st.code(current_project_name())

        if st.button("Close project", use_container_width=True):
            reset_uploaded_project()
            st.rerun()

        st.divider()
        project_downloads(
            extension_path=extension_path,
            proposal_path=Path(st.session_state["project_proposal_path"]),
        )

        st.divider()
        st.header("View")

        view_mode = st.radio(
            "Visualization mode",
            options=[
                "Conceptual View",
                "Detailed OWL View",
            ],
            index=0,
        )

        detailed_mode = view_mode == "Detailed OWL View"

        st.divider()
        st.header("Complexity")

        complexity = st.slider(
            "Structural complexity",
            min_value=1,
            max_value=6,
            value=3,
            step=1,
        )

        complexity_names = {
            1: "Main concepts",
            2: "+ Class hierarchy",
            3: "+ Concept relations",
            4: "+ Attributes",
            5: "+ Cardinalities and constraints",
            6: "+ Examples and semantic detail",
        }

        st.caption(complexity_names[complexity])

        show_attributes = st.checkbox(
            "Show schema attributes",
            value=complexity >= 4,
            disabled=complexity < 4,
        )

        st.divider()
        st.header("User-added nodes")

        show_user_nodes = st.checkbox(
            "Show user-added instances",
            value=True,
        )

        show_instance_fields = st.checkbox(
            "Show instance field values as nodes",
            value=False,
            disabled=not show_user_nodes,
        )

        st.divider()
        st.header("Graph display")

        graph_height = st.slider(
            "Graph height",
            min_value=650,
            max_value=1400,
            value=900,
            step=50,
            help=(
                "Controls the graph height inside the page. "
                "Use Fullscreen in the graph toolbar for presentation and snapshots."
            ),
        )

        st.caption(
            "V6 adds Fullscreen, Fit, and 4K PNG export controls directly on the graph."
        )

    ontology_graph = build_ontology_graph(
        rdf_graph=rdf_graph,
        complexity=complexity,
        detailed_mode=detailed_mode,
        show_attributes=show_attributes,
    )

    if show_user_nodes:
        add_named_individuals_to_graph(
            schema_graph=ontology_graph,
            rdf_graph=rdf_graph,
            detailed_mode=detailed_mode,
            only_user_extensions=True,
            extension_graph=extension_graph,
            show_fields=show_instance_fields,
        )

    class_options = []

    for node_id_value, data in ontology_graph.nodes(data=True):
        if data.get("node_type") != "class":
            continue

        class_options.append(
            (
                node_id_value,
                data.get("label", node_id_value),
            )
        )

    class_options.sort(key=lambda item: item[1].casefold())

    focus_lookup = {"Full ontology": None}
    for identifier, label in class_options:
        focus_lookup[label] = identifier

    with st.sidebar:
        st.divider()
        st.header("Explore")

        focus_label = st.selectbox(
            "Focus concept",
            options=list(focus_lookup.keys()),
        )

        focus_node = focus_lookup[focus_label]

        neighborhood_depth = st.slider(
            "Neighborhood depth",
            min_value=0,
            max_value=4,
            value=2,
            step=1,
            disabled=focus_node is None,
        )

    display_graph = filter_by_focus(
        graph=ontology_graph,
        focus_node=focus_node,
        depth=neighborhood_depth,
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Visible concepts", display_graph.number_of_nodes())
    with col2:
        st.metric("Visible relations", display_graph.number_of_edges())
    with col3:
        st.metric("OWL classes", len(get_classes(rdf_graph)))
    with col4:
        st.metric("Ontology triples", len(rdf_graph))

    if view_mode == "Conceptual View":
        st.info(
            "Conceptual View simplifies the OWL representation so that "
            "the ontology can be explained to people without OWL knowledge."
        )
    else:
        st.info(
            "Detailed OWL View exposes additional ontology information "
            "for technical inspection while keeping the visible labels in English."
        )

    network = build_pyvis(
        display_graph,
        height_px=max(600, graph_height - 20),
    )
    render_network(
        network,
        component_height=graph_height,
    )

    with st.expander("Legend", expanded=False):
        st.markdown(
            """
**Node families**

- 🔵 **General Node** — technical superclass retained for V5 compatibility
- 🟢 **Food-system actors** — input manufacturers, producers, food companies, retail / food service, and consumers
- 🟢 **Institutional actors** — education, government, finance, and NGOs
- 🔷 **Flows** — product flow and influence / demand flow
- 🟡 **Resources** — inputs, primary food / biomass, and food products
- 🟠 **One Health outcomes** — human health / nutrition and environmental / natural-resource outcomes
- ⚪ **System context** — regulatory, institutional, social, and food environment concepts
- 🟣 **Attributes**
- 🔶 **Ontology examples / named individuals**
- ⭐ **User-added nodes** — nodes created through the builder

**Relations**

- **is a** — class specialization
- **solid relation** — semantic/object relation
- **dashed relation** — attribute, restriction, or instance relation
- **[1]** — exactly one
- **[0..1]** — zero or one
- **[1..*]** — one or more
            """
        )

    st.subheader("Concept Inspector")

    inspector_options = {
        data.get("label", node_id_value): node_id_value
        for node_id_value, data in display_graph.nodes(data=True)
        if data.get("node_type") in {"class", "individual"}
    }

    if inspector_options:
        selected_label = st.selectbox(
            "Select a concept or instance to inspect",
            options=sorted(
                inspector_options.keys(),
                key=str.casefold,
            ),
        )

        selected_id = inspector_options[selected_label]
        selected_data = display_graph.nodes[selected_id]

        inspector_left, inspector_right = st.columns([1, 2])

        with inspector_left:
            category = selected_data.get("category", "concept")
            st.markdown(f"### {selected_label}")
            st.write(
                f"**Family:** "
                f"{category.replace('_', ' ').title()}"
            )

        with inspector_right:
            st.text(selected_data.get("title", ""))

    with st.expander("Ontology technical summary", expanded=False):
        st.write(f"Classes: {len(get_classes(rdf_graph))}")
        st.write(
            f"Object properties: {len(get_object_properties(rdf_graph))}"
        )
        st.write(
            f"Datatype properties: {len(get_datatype_properties(rdf_graph))}"
        )
        st.write(f"Named individuals: {len(get_individuals(rdf_graph))}")
        st.write(f"Triples: {len(rdf_graph)}")
        st.write(
            f"User-added individuals: {len(get_individuals(extension_graph))}"
        )


# ============================================================
# Main
# ============================================================


def main() -> None:
    """Application entry point for local use and Streamlit Community Cloud."""

    render_tutorial_download()
    st.sidebar.divider()

    st.sidebar.header("Open ontology")
    uploaded_file = st.sidebar.file_uploader(
        "Upload a Turtle ontology (.ttl)",
        type=["ttl"],
        accept_multiple_files=False,
        help=(
            "Choose an ontology from your computer. "
            "This public application contains no default ontology."
        ),
    )

    if uploaded_file is None:
        st.title("Ontology Explorer V8.1 — Cloud")
        st.subheader("Blank project")
        st.write(
            "Upload a Turtle (.ttl) ontology from your computer to start. "
            "Nothing is loaded by default."
        )
        st.info(
            "The GitHub repository contains only the application code and its "
            "dependencies. Uploaded ontologies are processed only for the current "
            "application session."
        )
        st.markdown(
            """
**Workflow**

1. Upload a `.ttl` ontology.
2. Explore its classes, hierarchy, properties, restrictions, and individuals.
3. Open the **Interactive Graph Editor** to add nodes and relations visually.
4. Export either the modifications layer or the complete merged TTL from the editor.
            """
        )
        return

    try:
        ttl_path, extension_path, proposal_path = uploaded_project_paths(
            uploaded_file
        )

        base_graph = load_base_ontology(str(ttl_path))
        instance_graph = load_extension_graph(extension_path)
        proposal_state = load_proposal_state(proposal_path)
        proposal_graph = build_proposal_graph(
            base_graph,
            ttl_path,
            proposal_state,
        )

        merged_without_proposal = combined_graph(
            base_graph,
            instance_graph,
        )
        merged_graph = combined_graph(
            merged_without_proposal,
            proposal_graph,
        )
    except Exception as exc:
        st.error(
            "The uploaded file could not be parsed as a Turtle ontology."
        )
        st.exception(exc)
        return

    st.sidebar.success(f"Loaded: {current_project_name()}")

    view = get_query_value("view", "explorer")

    if view == "editor":
        render_direct_graph_editor(
            base_graph=base_graph,
            extension_graph=instance_graph,
            merged_graph=merged_graph,
            ttl_path=ttl_path,
            extension_path=extension_path,
        )
    else:
        render_explorer(
            base_graph=base_graph,
            extension_graph=instance_graph,
            rdf_graph=merged_graph,
            ttl_path=ttl_path,
            extension_path=extension_path,
        )


if __name__ == "__main__":
    main()
