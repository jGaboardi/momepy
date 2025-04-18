#!/usr/bin/env python
import math
import os
import warnings

import geopandas as gpd
import libpysal
import networkx as nx
import numpy as np
import pandas as pd
from shapely.geometry import Point

__all__ = [
    "unique_id",
    "gdf_to_nx",
    "nx_to_gdf",
    "limit_range",
]


def deprecated(new_way):
    """
    Decorator to mark classes as deprecated and point towards functional API.
    """

    def decorator(func1):
        import functools

        @functools.wraps(func1)
        def new_func1(*args, **kwargs):
            if os.getenv("ALLOW_LEGACY_MOMEPY", "False").lower() not in (
                "true",
                "1",
                "yes",
            ):
                warnings.warn(
                    f"Class based API like `momepy.{func1.__name__}` is deprecated. "
                    f"Replace it with `momepy.{new_way}` to use functional API instead "
                    "or pin momepy version <1.0. Class-based API will be removed in "
                    "1.0. "
                    # "See details at https://docs.momepy.org/en/stable/migration.html",
                    "",
                    FutureWarning,
                    stacklevel=2,
                )
            return func1(*args, **kwargs)

        return new_func1

    return decorator


def removed(new_way):
    """
    Decorator to mark classes as deprecated and removed from momepy.
    """

    def decorator(func1):
        import functools

        @functools.wraps(func1)
        def new_func1(*args, **kwargs):
            if os.getenv("ALLOW_LEGACY_MOMEPY", "False").lower() not in (
                "true",
                "1",
                "yes",
            ):
                warnings.warn(
                    f"`momepy.{func1.__name__}` is deprecated. Replace it with "
                    f"{new_way} "
                    "or pin momepy version <1.0. This class will be removed in 1.0. "
                    # "See details at https://docs.momepy.org/en/stable/migration.html"
                    "",
                    FutureWarning,
                    stacklevel=2,
                )
            return func1(*args, **kwargs)

        return new_func1

    return decorator


def unique_id(objects):
    """
    Add an attribute with a unique ID to each row of a GeoDataFrame.

    Parameters
    ----------
    objects : GeoDataFrame
        A GeoDataFrame containing objects to analyse.

    Returns
    -------
    series : Series
        A Series containing resulting values.

    """
    series = range(len(objects))
    return series


def _angle(a, b, c):
    """
    Measure the angle between a-b, b-c (in degrees). Helper for ``gdf_to_nx``.
    Adapted from cityseer's implementation.
    """
    a1 = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    a2 = math.degrees(math.atan2(c[1] - b[1], c[0] - b[0]))
    return abs((a2 - a1 + 180) % 360 - 180)


def _generate_primal(
    graph, gdf_network, fields, multigraph, oneway_column=None, preserve_index=False
):
    """Generate a primal graph. Helper for ``gdf_to_nx``."""
    graph.graph["approach"] = "primal"

    if gdf_network.index.name is not None:
        graph.graph["index_name"] = gdf_network.index.name

    msg = (
        " This can lead to unexpected behaviour. "
        "The intended usage of the conversion function "
        "is with networks made of LineStrings only."
    )

    if "LineString" not in gdf_network.geom_type.unique():
        warnings.warn(
            message="The given network does not contain any LineString." + msg,
            category=RuntimeWarning,
            stacklevel=3,
        )

    if len(gdf_network.geom_type.unique()) > 1:
        warnings.warn(
            message="The given network consists of multiple geometry types." + msg,
            category=RuntimeWarning,
            stacklevel=3,
        )
    custom_index = not gdf_network.index.equals(pd.RangeIndex(len(gdf_network)))

    for i, row in enumerate(gdf_network.itertuples()):
        first = row.geometry.coords[0]
        last = row.geometry.coords[-1]

        data = list(row)[1:]
        attributes = dict(zip(fields, data, strict=True))
        if preserve_index:
            attributes["index_position"] = i
            if custom_index:
                attributes["index"] = row.Index
        if multigraph:
            graph.add_edge(first, last, **attributes)

            if oneway_column:
                oneway = bool(getattr(row, oneway_column))
                if not oneway:
                    graph.add_edge(last, first, **attributes)
        else:
            graph.add_edge(first, last, **attributes)

    node_attrs = {node: {"x": node[0], "y": node[1]} for node in graph.nodes}
    nx.set_node_attributes(graph, node_attrs)


def _generate_dual(
    graph, gdf_network, fields, angles, multigraph, angle, preserve_index
):
    """Generate a dual graph. Helper for ``gdf_to_nx``."""
    graph.graph["approach"] = "dual"

    if gdf_network.index.name is not None:
        graph.graph["index_name"] = gdf_network.index.name

    custom_index = not gdf_network.index.equals(pd.RangeIndex(len(gdf_network)))

    key = 0

    sw = libpysal.weights.Queen.from_dataframe(
        gdf_network, silence_warnings=True, use_index=False
    )
    cent = gdf_network.geometry.centroid
    gdf_network["temp_x_coords"] = cent.x
    gdf_network["temp_y_coords"] = cent.y

    for i, row in enumerate(gdf_network.itertuples()):
        centroid = (row.temp_x_coords, row.temp_y_coords)
        data = list(row)[1:-2]
        attributes = dict(zip(fields, data, strict=True))
        if preserve_index:
            attributes["index_position"] = i
            if custom_index:
                attributes["index"] = row.Index
        graph.add_node(centroid, **attributes)

        if sw.cardinalities[i] > 0:
            for n in sw.neighbors[i]:
                start = centroid
                end = (
                    gdf_network["temp_x_coords"].iloc[n],
                    gdf_network["temp_y_coords"].iloc[n],
                )
                p0 = row.geometry.coords[0]
                p1 = row.geometry.coords[-1]
                geom = gdf_network.geometry.iloc[n]
                p2 = geom.coords[0]
                p3 = geom.coords[-1]
                points = [p0, p1, p2, p3]
                shared = [x for x in points if points.count(x) > 1]
                if shared:  # fix for non-planar graph
                    remaining = [e for e in points if e not in [shared[0]]]
                    if len(remaining) == 2:
                        if angles:
                            angle_value = _angle(remaining[0], shared[0], remaining[1])
                            if multigraph:
                                graph.add_edge(
                                    start, end, key=0, **{angle: angle_value}
                                )
                                key += 1
                            else:
                                graph.add_edge(start, end, **{angle: angle_value})
                        else:
                            if multigraph:
                                graph.add_edge(start, end, key=0)
                                key += 1
                            else:
                                graph.add_edge(start, end)


def gdf_to_nx(
    gdf_network,
    approach="primal",
    length="mm_len",
    multigraph=True,
    directed=False,
    angles=True,
    angle="angle",
    oneway_column=None,
    integer_labels=False,
    preserve_index=False,
):
    """
    Convert a LineString GeoDataFrame to a ``networkx.MultiGraph`` or other
    Graph as per specification. Columns are preserved  as edge or node
    attributes (depending on the ``approach``). Index is not preserved.

    See the User Guide page :doc:`../../user_guide/graph/convert` for details.

    Parameters
    ----------
    gdf_network : GeoDataFrame
        A GeoDataFrame containing objects to convert.
    approach : str, default 'primal'
        Allowed options are ``'primal'`` or ``'dual'``. Primal graphs represent
        endpoints as nodes and LineStrings as edges. Dual graphs represent
        LineStrings as nodes and their topological relation as edges. In such a
        case, it can encode an angle between LineStrings as an edge attribute.
    length : str, default 'mm_len'
        The attribute name of segment length (geographical)
        which will be saved to the graph.
    multigraph : bool, default True
        Create a ``MultiGraph`` of ``Graph`` (potentially directed).
        ``MutliGraph`` allows multiple edges between any pair of nodes,
        which is a common case in street networks.
    directed : bool, default False
        Create a directed graph (``DiGraph`` or ``MultiDiGraph``).
        Directionality follows the order of LineString coordinates.
    angles : bool, default True
        Capture the angles between LineStrings as an attribute of a dual graph.
        Ignored if ``approach='primal'``.
    angle : str, default 'angle'
        The attribute name of the angle between LineStrings which will
        be saved to the graph. Ignored if ``approach='primal'``.
    oneway_column : str, default None
        Create an additional edge for each LineString which allows bidirectional
        path traversal by specifying the boolean column in the GeoDataFrame. Note,
        that the reverse conversion ``nx_to_gdf(gdf_to_nx(gdf, directed=True,
        oneway_column="oneway"))`` will contain additional duplicated geometries.
    integer_labels : bool, default False
        Convert node labels to integers. By default, node labels are tuples with (x, y)
        coordinates. Set to True to encode them as integers. Note that the x, and y
        coordinates are always preserved as node attributes.
    preserve_index : bool, default False
        Preserve information about the index of ``gdf_network``. If
        ``gdf_network.index`` is the default ``RangeIndex``, ``"index_position"``
        attribute is added to each edge. If it is a custom index, ``"index_position"``
        and ``"index"`` attributes are added. These attributes are then used by
        :func:`nx_to_gdf` to faithfully roundtrip the data in the same order.

    Returns
    -------
    net : networkx.Graph, networkx.MultiGraph, networkx.DiGraph, networkx.MultiDiGraph
        Graph as per specification.

    See also
    --------
    nx_to_gdf

    Examples
    --------
    >>> import geopandas as gpd
    >>> df = gpd.read_file(momepy.datasets.get_path('bubenec'), layer='streets')
    >>> df.head(5)
                                                geometry
    0  LINESTRING (1603585.640 6464428.774, 1603413.2...
    1  LINESTRING (1603268.502 6464060.781, 1603296.8...
    2  LINESTRING (1603607.303 6464181.853, 1603592.8...
    3  LINESTRING (1603678.970 6464477.215, 1603675.6...
    4  LINESTRING (1603537.194 6464558.112, 1603557.6...

    Primal graph:

    >>> G = momepy.gdf_to_nx(df)
    >>> G
    <networkx.classes.multigraph.MultiGraph object at 0x7f8cf90fad50>

    >>> G_directed = momepy.gdf_to_nx(df, directed=True)
    >>> G_directed
    <networkx.classes.multidigraph.MultiDiGraph object at 0x7f8cf90f56d0>

    >>> G_digraph = momepy.gdf_to_nx(df, multigraph=False, directed=True)
    >>> G_digraph
    <networkx.classes.digraph.DiGraph object at 0x7f8cf9150c10>

    >>> G_graph = momepy.gdf_to_nx(df, multigraph=False, directed=False)
    >>> G_graph
    <networkx.classes.graph.Graph object at 0x7f8cf90facd0>

    Dual graph:

    >>> G_dual = momepy.gdf_to_nx(df, approach="dual")
    >>> G_dual
    <networkx.classes.multigraph.MultiGraph object at 0x7f8cf9150fd0>

    """
    gdf_network = gdf_network.copy()
    if "key" in gdf_network.columns:
        gdf_network.rename(columns={"key": "__key"}, inplace=True)

    if multigraph and directed:
        net = nx.MultiDiGraph()
    elif multigraph and not directed:
        net = nx.MultiGraph()
    elif not multigraph and directed:
        net = nx.DiGraph()
    else:
        net = nx.Graph()

    net.graph["crs"] = gdf_network.crs
    gdf_network[length] = gdf_network.geometry.length
    fields = list(gdf_network.columns)

    if approach == "primal":
        if oneway_column and not directed:
            raise ValueError(
                "Bidirectional lines are only supported for directed graphs."
            )

        _generate_primal(
            net,
            gdf_network,
            fields,
            multigraph,
            oneway_column,
            preserve_index=preserve_index,
        )

    elif approach == "dual":
        if directed:
            raise ValueError("Directed graphs are not supported in dual approach.")

        _generate_dual(
            net,
            gdf_network,
            fields,
            angles=angles,
            multigraph=multigraph,
            angle=angle,
            preserve_index=preserve_index,
        )

    else:
        raise ValueError(
            f"Approach '{approach}' is not supported. Use 'primal' or 'dual'."
        )

    if integer_labels:
        net = nx.convert_node_labels_to_integers(net)

    return net


def _points_to_gdf(net):
    """Generate a point gdf from nodes. Helper for ``nx_to_gdf``."""
    node_xy, node_data = zip(*net.nodes(data=True), strict=True)
    if isinstance(node_xy[0], int) and "x" in node_data[0]:
        geometry = [Point(data["x"], data["y"]) for data in node_data]  # osmnx graph
    else:
        geometry = [Point(*p) for p in node_xy]
    gdf_nodes = gpd.GeoDataFrame(list(node_data), geometry=geometry)
    if "crs" in net.graph:
        gdf_nodes.crs = net.graph["crs"]
    return gdf_nodes


def _lines_to_gdf(net, points, node_id):
    """Generate a linestring gdf from edges. Helper for ``nx_to_gdf``."""
    starts, ends, edge_data = zip(*net.edges(data=True), strict=True)
    gdf_edges = gpd.GeoDataFrame(list(edge_data))

    if points is True:
        gdf_edges["node_start"] = [net.nodes[s][node_id] for s in starts]
        gdf_edges["node_end"] = [net.nodes[e][node_id] for e in ends]

    if "crs" in net.graph:
        gdf_edges.crs = net.graph["crs"]
    if "index_position" in gdf_edges.columns:
        gdf_edges = gdf_edges.sort_values("index_position").drop(
            columns="index_position"
        )
    if "index" in gdf_edges.columns:
        gdf_edges = gdf_edges.set_index("index")
    else:
        gdf_edges = gdf_edges.reset_index(drop=True)
    gdf_edges.index.name = net.graph.get("index_name", None)

    return gdf_edges


def _primal_to_gdf(net, points, lines, spatial_weights, node_id):
    """Generate gdf(s) from a primal network. Helper for ``nx_to_gdf``."""
    if points is True:
        gdf_nodes = _points_to_gdf(net)

        if spatial_weights is True:
            weights = libpysal.weights.W.from_networkx(net)
            weights.transform = "b"

    if lines is True:
        gdf_edges = _lines_to_gdf(net, points, node_id)

    if points is True and lines is True:
        if spatial_weights is True:
            return gdf_nodes, gdf_edges, weights
        return gdf_nodes, gdf_edges
    if points is True and lines is False:
        if spatial_weights is True:
            return gdf_nodes, weights
        return gdf_nodes
    return gdf_edges


def _dual_to_gdf(net):
    """Generate a linestring gdf from a dual network. Helper for ``nx_to_gdf``."""
    starts, edge_data = zip(*net.nodes(data=True), strict=True)
    gdf_edges = gpd.GeoDataFrame(list(edge_data))
    if "index_position" in gdf_edges.columns:
        gdf_edges = gdf_edges.sort_values("index_position").drop(
            columns="index_position"
        )
    if "index" in gdf_edges.columns:
        gdf_edges = gdf_edges.set_index("index")
    else:
        gdf_edges = gdf_edges.reset_index(drop=True)
    gdf_edges.index.name = net.graph.get("index_name", None)
    gdf_edges.crs = net.graph["crs"]
    return gdf_edges


def nx_to_gdf(
    net,
    points=True,
    lines=True,
    spatial_weights=False,
    nodeID="nodeID",  # noqa: N803
):
    """
    Convert a ``networkx.Graph`` to a LineString GeoDataFrame and Point GeoDataFrame.

    Automatically detects an ``approach`` of the graph and assigns
    edges and nodes to relevant geometry type.

    See the User Guide page :doc:`../../user_guide/graph/convert` for details.

    Parameters
    ----------
    net : networkx.Graph
        A ``networkx.Graph`` object.
    points : bool (default is ``True``)
        Export point-based gdf representing intersections.
    lines : bool (default is ``True``)
        Export line-based gdf representing streets.
    spatial_weights : bool (default is ``False``)
        Set to ``True`` to export a libpysal spatial weights
        for nodes (only for primal graphs).
    nodeID : str
        The name of the node ID column to be generated.

    Returns
    -------
    GeoDataFrame
       The  Selected gdf or tuple of both gdfs or tuple of gdfs and weights.

    See also
    --------
    gdf_to_nx

    Examples
    --------
    >>> import geopandas as gpd
    >>> df = gpd.read_file(momepy.datasets.get_path('bubenec'), layer='streets')
    >>> df.head(2)
                                                geometry
    0  LINESTRING (1603585.640 6464428.774, 1603413.2...
    1  LINESTRING (1603268.502 6464060.781, 1603296.8...
    >>> G = momepy.gdf_to_nx(df)

    Converting the primal Graph to points as intersections and lines as street segments:

    >>> points, lines = momepy.nx_to_gdf(graph)
    >>> points.head(2)
       nodeID                         geometry
    0       1  POINT (1603585.640 6464428.774)
    1       2  POINT (1603413.206 6464228.730)
    >>> lines.head(2)
                         geometry      mm_len  node_start  node_end
    0  LINESTRING (1603585.640...  264.103950           1         2
    1  LINESTRING (1603561.740...   70.020202           1         9

    Storing the relationship between points/nodes as a libpysal W object:

    >>> points, lines, W = momepy.nx_to_gdf(graph, spatial_weights=True)
    >>> W
    <libpysal.weights.weights.W object at 0x7f8d01837210>

    Converting the dual Graph to lines. The dual Graph does not export edges to GDF:

    >>> G = momepy.gdf_to_nx(df, approach="dual")
    >>> lines = momepy.nx_to_gdf(graph)
    >>> lines.head(2)
                                                geometry      mm_len
    0  LINESTRING (1603585.640 6464428.774, 1603413.2...  264.103950
    1  LINESTRING (1603607.303 6464181.853, 1603592.8...  199.746503
    """
    # generate nodes and edges geodataframes from graph
    primal = None
    if "approach" in net.graph:
        if net.graph["approach"] == "primal":
            primal = True
        elif net.graph["approach"] == "dual":
            return _dual_to_gdf(net)
        else:
            raise ValueError(
                f"Approach '{net.graph['approach']}' is not supported. "
                "Use 'primal' or 'dual'."
            )

    if not primal:
        warnings.warn(
            message="Approach is not set. Defaulting to 'primal'.",
            category=UserWarning,
            stacklevel=2,
        )

    for nid, n in enumerate(net):
        net.nodes[n][nodeID] = nid

    return _primal_to_gdf(
        net,
        points=points,
        lines=lines,
        spatial_weights=spatial_weights,
        node_id=nodeID,
    )


def limit_range(vals, rng):
    """
    Extract values within selected range.

    Parameters
    ----------
    vals : numpy.array
        Values over which to extract a range.
    rng : tuple, list, optional (default None)
        A two-element sequence containing floats between 0 and 100 (inclusive)
        that are the percentiles over which to compute the range.
        The order of the elements is not important.

    Returns
    -------
    vals : numpy.array
        The limited array.
    """

    nan_tracker = np.isnan(vals)

    if (len(vals) > 2) and (not nan_tracker.all()):
        method = {"method": "nearest"}
        rng = sorted(rng)
        if nan_tracker.any():
            lower, higher = np.nanpercentile(vals, rng, **method)
        else:
            lower, higher = np.percentile(vals, rng, **method)
        vals = vals[(lower <= vals) & (vals <= higher)]

    return vals


def _azimuth(point1, point2):
    """Return the azimuth between 2 shapely points (interval 0 - 180)."""
    angle = np.arctan2(point2[0] - point1[0], point2[1] - point1[1])
    return np.degrees(angle) % 180
