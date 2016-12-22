#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2014-2016 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It provides functions to plot dispatcher map and workflow.
"""

import graphviz as gviz
import os.path as osp
import string
import urllib.parse as urlparse
import pprint
import inspect
import platform
import copy
import tempfile
import html
import logging
import functools
import itertools
import regex
import socket
import datetime
import os
import jinja2
import pkg_resources
import glob
import shutil
import weakref
import flask
import collections
from docutils import nodes
from .cst import START, SINK, END, EMPTY, SELF, NONE, PLOT
from .dsp import SubDispatch, combine_dicts, map_dict, combine_nested_dicts, \
    selector
from .des import parent_func, search_node_description
from .alg import stlp
from .gen import counter


__author__ = 'Vincenzo Arcidiacono'

__all__ = ['SiteMap']

log = logging.getLogger(__name__)

PLATFORM = platform.system().lower()

_UNC = u'\\\\?\\' if PLATFORM == 'windows' else ''


class DspPlot(gviz.Digraph):
    def __init__(self, sitemap, *args, **kwargs):
        super(DspPlot, self).__init__(*args, **kwargs)
        self.sitemap = sitemap

    @property
    def filepath(self):
        return uncpath(os.path.join(self.directory, self.filename))


def uncpath(p):
    return _UNC + osp.abspath(p)


def _encode_file_name(s):
    """
    Take a string and return a valid filename constructed from the string.

    Uses a whitelist approach: any characters not present in valid_chars are
    removed. Also spaces are replaced with underscores.
    """

    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    filename = ''.join(c for c in s if c in valid_chars)
    filename = filename.replace(' ', '_')  # I don't like spaces in filenames.
    return filename


def _upt_styles(styles, base=None):
    d, base = {}, copy.deepcopy(base or {})
    res = {}
    for i in ('info', 'warning', 'error'):
        combine_nested_dicts(base.get(i, {}), styles.get(i, {}), base=d)
        res[i] = copy.deepcopy(d)
    return res


def autoplot_function(kwargs):
    keys = sorted(kwargs, key=lambda x: (x is not PLOT, x))
    kw = combine_dicts(*selector(keys, kwargs, output_type='list'))
    return functools.partial(kw.pop('obj').plot, **kw)


def autoplot_callback(value):
    value()


class _Table(nodes.General, nodes.Element):
    tagname = 'TABLE'

    def adds(self, *items):
        for item in items:
            self += item
        return self


class _Tr(_Table):
    tagname = 'TR'

    def add(self, text, **attributes):
        self += _Td(**attributes).add(text)
        return self


class _Td(nodes.General, nodes.Element):
    tagname = 'TD'

    def add(self, text):
        self += nodes.Text(html.escape(text).replace('\n', '<BR/>'))
        return self


def jinja2_format(source, context=None, **kw):
    return jinja2.Environment(**kw).from_string(source).render(context or {})


def valid_filename(item, filenames, ext=None):
    if ext == '':
        _ = '%s'
    else:
        _ = '%s.{}'.format(ext or item.ext)
    if isinstance(item, str):
        _filename = item
    else:
        _filename = item._filename

    filename, c = _ % _filename, counter()
    while filename in filenames:
        filename = _ % '{}-{}'.format(_filename, c())
    return filename


def update_filenames(node, filenames):
    filename = valid_filename(node, filenames)
    yield (node, None), filename
    filenames.append(filename)
    for file in node.extra_files:
        filename, ext = osp.splitext(file)
        filename = valid_filename(filename, filenames, ext=ext[1:])
        yield (node, file), filename
        filenames.append(filename)


def site_view(app, node, filepath, context, generated_files):
    static_folder = app.static_folder
    fpath = osp.join(static_folder, filepath)
    if not osp.isfile(fpath):
        generated_files.extend(node.view(fpath, context))
    fpath = osp.relpath(fpath, static_folder).replace('\\', '/')
    return app.send_static_file(fpath)


def render_output(out, pformat):
    out = parent_func(out)
    if inspect.isfunction(out):
        # noinspection PyBroadException
        try:
            out = inspect.getsource(out)
        except:
            pass

    if isinstance(out, (datetime.datetime, datetime.timedelta)):
        out = str(out)

    if isinstance(out, str):
        return out

    return pformat(out)


class SiteNode(object):
    counter = counter()
    ext = 'txt'
    pprint = pprint.PrettyPrinter(compact=True, width=200)

    def __init__(self, folder, node_id, item):
        self.folder = folder
        self.node_id = node_id
        self.item = item
        self.id = str(self.counter())
        self.extra_files = []

    @property
    def name(self):
        try:
            return parent_func(self.item).__name__
        except AttributeError:
            return self.node_id

    @property
    def title(self):
        return self.name

    @property
    def _filename(self):
        return _encode_file_name(self.title)

    @property
    def filename(self):
        return '.'.join((self._filename, self.ext))

    def __repr__(self):
        return self.title

    def render(self, *args, **kwargs):
        return render_output(self.item, self.pprint.pformat)

    def view(self, filepath, *args, **kwargs):
        filepath = uncpath(filepath)
        os.makedirs(osp.dirname(filepath), exist_ok=True)
        with open(filepath, 'w') as f:
            f.write(self.render(*args, **kwargs))
        return filepath,


class FolderNode(object):
    counter = counter()

    node_styles = _upt_styles({
        'info': {
            START: {'shape': 'egg', 'fillcolor': 'red', 'label': 'start'},
            SELF: {'shape': 'egg', 'fillcolor': 'gold', 'label': 'self'},
            PLOT: {'shape': 'egg', 'fillcolor': 'gold', 'label': 'plot'},
            END: {'shape': 'egg', 'fillcolor': 'blue', 'label': 'end'},
            EMPTY: {'shape': 'egg', 'fillcolor': 'gray', 'label': 'empty'},
            SINK: {'shape': 'egg', 'fillcolor': 'black', 'fontcolor': 'white',
                   'label': 'sink'},
            NONE: {
                'data': {'shape': 'box', 'style': 'rounded,filled',
                         'fillcolor': 'cyan'},
                'function': {'shape': 'box', 'fillcolor': 'springgreen'},
                'subdispatch': {'shape': 'note', 'style': 'filled',
                                'fillcolor': 'yellow'},
                'subdispatchfunction': {'shape': 'note', 'style': 'filled',
                                        'fillcolor': 'yellowgreen'},
                'subdispatchpipe': {'shape': 'note', 'style': 'filled',
                                    'fillcolor': 'greenyellow'},
                'dispatcher': {'shape': 'note', 'style': 'filled',
                               'fillcolor': 'springgreen'},
                'edge': {None: None}
            }
        },
        'warning': {
            NONE: {
                'data': {'fillcolor': 'orange'},
                'function': {'fillcolor': 'orange'},
                'subdispatch': {'fillcolor': 'orange'},
                'subdispatchfunction': {'fillcolor': 'orange'},
                'subdispatchpipe': {'fillcolor': 'orange'},
                'dispatcher': {'fillcolor': 'orange'},
            }
        },
        'error': {
            NONE: {
                'data': {'fillcolor': 'red'},
                'function': {'fillcolor': 'red'},
                'subdispatch': {'fillcolor': 'red'},
                'subdispatchfunction': {'fillcolor': 'red'},
                'subdispatchpipe': {'fillcolor': 'red'},
                'dispatcher': {'fillcolor': 'red'},
            }
        }
    })

    node_data = (
        '-', '.tooltip', '!default_values', 'wait_inputs', '+function',
        'weight', 'remote_links', 'distance', '!error', '*output'
    )

    node_function = (
        '-', '.tooltip', '+input_domain', 'weight',
        'missing_inputs_outputs', 'distance', 'started', 'duration', '!error',
        '*function'
    )

    edge_data = ('?', 'inp_id', 'out_id', 'weight')

    node_map = {
        '-': (), # Add title.
        '?': (), # Optional title.
        '': ('dot', 'table'), # item in the table.
        '+': ('dot', 'table'), # link.
        '!': ('dot', 'table'), # if str is big add a link, otherwise table.
        '.': ('dot',),  # dot attr
        '*': ('link',) # title link
    }
    re_node = regex.compile('^([.*+!]?)(\w+)$')
    max_lines = 5
    max_width = 200
    pprint = pprint.PrettyPrinter(compact=True, width=200)

    def __init__(self, folder, node_id, attr, **options):
        self.folder = folder
        self.node_id = node_id
        attr = attr.copy()
        if self.folder.workflow:
            key_m = {'solution_domain': 'input_domain', 'solution': 'function'}
            for k in key_m.values():
                attr.pop(k, None)
            attr = map_dict(key_m, attr)
        self.attr = attr
        self.id = str(self.counter())
        self._links = {}
        for k, v in options.items():
            setattr(self, k, v)

    @property
    def title(self):
        return self.node_id

    @property
    def type(self):
        return self.attr.get('type', 'data')

    def __repr__(self):
        return self.title

    def yield_attr(self, name, *args, **kwargs):
        try:
            yield name, self.attr[name]
        except KeyError:
            pass

    def render_size(self, out):
        lines = render_output(out, self.pprint.pformat).splitlines(True)
        n, w = self.max_lines, self.max_width
        return len(lines) <= n and not any(len(l) > w for l in lines)

    def items(self):
        check = self.render_size
        for k, func in self.render_funcs():
            if k and k in '*+':
                yield from func()
            elif k =='!':
                yield from ((i, j)for i, j in func() if not check(j))

    def _tooltip(self):
        try:
            tooltip = search_node_description(
                self.node_id, self.attr, self.folder.dsp
            )[0]
        except (AttributeError, KeyError):
            tooltip = None
        yield 'tooltip', tooltip or self.title

    def _wait_inputs(self):
        attr = self.attr
        try:
            if attr['type'] == 'data' and attr['wait_inputs']:
                yield 'wait_inputs', True
        except KeyError:
            pass

    def _default_values(self):
        try:
            dfl = self.folder.dsp.default_values.get(self.node_id, {})
            res = map_dict({'value': 'default'}, dfl)

            if not res.get('initial_dist', 1):
                res.pop('initial_dist')
        except AttributeError:
            res = {}
        yield from sorted(res.items())

    def _remote_links(self):
        attr, item = self.attr, self.folder.item
        for i, ((dsp_id, dsp), tag) in enumerate(attr.get('remote_links', [])):
            tag = {'child': 'outputs', 'parent': 'inputs'}[tag]
            dsp_attr, nid = dsp.nodes[dsp_id], self.node_id
            if tag == 'inputs':
                n = tuple(k for k, v in dsp_attr[tag].items() if nid in stlp(v))
            else:
                n = stlp(dsp_attr[tag][nid])

            if len(n) == 1:
                n = n[0]
            try:
                obj = item.sub_dsp[dsp]
            except (AttributeError, KeyError):
                obj = dsp

            n = '%s({})'.format(n)
            n = 'ref({}, "{}", "{}", attr)'.format(id(obj), dsp_id, n)
            yield 'remote %s %d' % (tag, i), '{{%s}}' %  n

    def _output(self):
        if self.node_id not in (START, SINK, SELF, END):
            try:
                out = self.folder.item[self.node_id]
                yield 'output', out
            except (KeyError, TypeError): # Output not in solution or item is not a solution.
                pass

    def _distance(self):
        try:
            yield 'distance', self.folder.item.dist[self.node_id]
        except (AttributeError, KeyError):
            pass

    def _weight(self):
        try:
            dsp = self.folder.dsp
            yield 'weight', dsp.node[self.node_id][dsp.weight]
        except (AttributeError, KeyError):
            pass

    def _missing_inputs_outputs(self):
        attr, res = self.attr, {}
        try:
            if attr['wait_inputs']:
                graph = self.folder.graph
                pred, succ = graph.pred[self.node_id], graph.succ[self.node_id]
                for i, j in (('inputs', pred), ('outputs', succ)):
                    v = tuple(k for k in attr[i] if k not in j)
                    if v:
                        yield 'M_%s' % i, v
        except (AttributeError, KeyError):
            pass

    def style(self):
        attr = self.attr

        if 'error' in attr:
            nstyle = 'error'
        elif list(self._missing_inputs_outputs()):
            nstyle = 'warning'
        else:
            nstyle = 'info'

        node_styles = self.node_styles.get(nstyle, self.node_styles['info'])
        if self.node_id in node_styles:
            node_style = node_styles[self.node_id].copy()
            node_style.pop(None, None)
            return node_style
        else:
            if self.type in ('dispatcher', 'function'):
                ntype = 'function',
                try:
                    func = parent_func(attr['function'])
                    ntype = (type(func).__name__.lower(),) + ntype
                except (KeyError, AttributeError):
                    pass
            elif self.type == 'edge':
                ntype = 'edge',
            else:
                ntype = 'data',
            for style in ntype:
                try:
                    node_style = node_styles[NONE][style].copy()
                    node_style.pop(None, None)
                    return node_style
                except KeyError:
                    pass

    def render_funcs(self):
        if self.type in ('dispatcher', 'function'):
            funcs = self.node_function
        elif self.type == 'edge':
            funcs = self.edge_data
        else:
            funcs = self.node_data
        r, s, match = {}, '_%s', self.re_node.match
        for f in funcs:
            if f == '-' or f =='?':
                yield f, lambda *args: self.title
            else:
                k, v = match(f).groups()
                try:
                    yield k, getattr(self, s % v)
                except AttributeError:
                    yield k, functools.partial(self.yield_attr, v)

    def ref(self, context, child, default, template='%s', attr=None):
        text, attr = template % default, attr or {}
        try:
            node, rule = context[child]
            attr = attr.copy()
            attr['href'] = urlparse.unquote('./%s' % osp.relpath(
                rule, osp.dirname(context[id(self.folder.item)][1])
            ).replace('\\', '/'))
            text = template % node.title
        except KeyError:
            pass

        return 'Td(**{}).add("{}")'.format(attr, text)

    def href(self, context, link_id):
        res = {}
        if link_id in self._links:
            node = self._links[link_id]
            res['text'] = node.title
            try:
                res['href'] = urlparse.unquote(
                    './%s' % osp.relpath(
                        context[(node, None)],
                        osp.dirname(context[(self.folder, None)])
                    ).replace('\\', '/')
                )
            except KeyError:
                pass
        return res

    def dot(self, context=None):
        if context is None:
            context = {}
        dot = self.style()
        if 'label' in dot:
            return dot
        key, val = dict(ALIGN="RIGHT", BORDER=1), dict(ALIGN="LEFT", BORDER=1)
        rows, funcs, cnt = [], list(self.render_funcs()), {'attr': val}
        cnt['ref'] = functools.partial(
            self.ref, {id(k.item): (k, v)
                       for (k, extra), v in context.items()
                       if not extra}
        )
        href, pformat, links = self.href, self.pprint.pformat, self._links
        for k, func in funcs:
            if k == '.':
                dot.update(func())
            elif not (k == '*' or k == '-' or k == '?'):
                for i, j in func():
                    tr = _Tr().add(i, **key)
                    if i in links and (k == '!' or k == '+'):
                        v = combine_dicts(val, {'text': j}, href(context, i))
                        tr.add(**v)
                    else:
                        j = render_output(j, pformat)
                        # noinspection PyBroadException
                        try:
                            tr += eval(jinja2_format(j, cnt))
                        except:  # It is not a valid jinja2 format.
                            tr.add(j, **val)

                    rows.append(tr)

        if any(k[0] == '-' or (rows and k[0] == '?') for k in funcs):
            link_id = next((next(f())[0] for k, f in funcs if k == '*'), None)
            kw = combine_dicts(
                self.href(context, link_id),
                {'COLSPAN': 2, 'BORDER': 0, 'text': self.title}
            )
            rows = [_Tr().add(**kw)] + rows

        if rows:
            k = 'xlabel' if self.type == 'edge' else 'label'
            dot[k] = '<%s>' % _Table(BORDER=0, CELLSPACING=0).adds(rows)

        return dot


class SiteFolder(object):
    counter = SiteNode.counter
    digraph = {
        'node_attr': {'style': 'filled'},
        'graph_attr': {},
        'edge_attr': {},
        'body': {'splines': 'ortho', 'style': 'filled'},
        'format': 'svg'
    }
    folder_node = FolderNode
    ext = 'svg'

    def __init__(self, item, dsp, graph, name='', workflow=False,
                 digraph=None, **options):
        self.item, self.dsp, self.graph = item, dsp, graph
        self._name = name
        self.workflow = workflow
        self.id = str(self.counter())
        self.options = options
        nodes = collections.OrderedDict(self._nodes)
        self.nodes = list(nodes.values())
        self.edges = [e for k, e in self._edges(nodes)]
        self.sitemap = None
        self.extra_files = []
        if digraph is not None:
            self.digraph = combine_dicts(self.__class__.digraph, digraph)

    @property
    def title(self):
        return self.name or ''

    @property
    def _filename(self):
        return _encode_file_name(self.title)

    @property
    def filename(self):
        return '.'.join((self._filename, self.ext))

    def __repr__(self):
        return self.title

    @property
    def inputs(self):
        try:
            from .sol import Solution
            if isinstance(self.item, Solution):
                return self.item.dsp.inputs or ()
            return self.item.inputs or ()
        except AttributeError:
            return ()

    @property
    def outputs(self):
        item = self.item
        if not isinstance(item, SubDispatch) or item.output_type != 'all':
            try:
                return item.outpus or ()
            except AttributeError:
                pass
        return ()

    @property
    def name(self):
        if not self._name:
            dsp = self.dsp
            name = dsp.name or '%s %d' % (type(dsp).__name__, id(dsp))
        else:
            name = self._name
        return name

    @property
    def label_name(self):
        return '-'.join(('workflow' if self.workflow else 'dmap', self.title))

    @property
    def _nodes(self):
        from networkx import is_isolate
        nodes, item, graph = self.dsp.nodes, self.item, self.graph
        try:
            errors = item._errors
        except AttributeError:
            errors = {}

        def nodes_filter(x):
            k, v = x
            return k in nodes and (k is not SINK or not is_isolate(graph, SINK))

        it = dict(filter(nodes_filter, graph.node.items()))
        if not nodes or not (graph.edge or self.inputs or self.outputs):
            it[EMPTY] = {'index': (EMPTY,)}

        if START in graph.node or (self.inputs and START not in graph.node):
            it[START] = {'index': (START,)}

        if self.outputs and END not in graph.node:
            it[END] = {'index': (END,)}

        for k, a in sorted(it.items()):
            attr = combine_dicts(nodes.get(k, {}), a)
            if k in errors:
                attr['error'] = errors[k]

            yield k, self.folder_node(self, k, attr, **self.options)

    def _edges(self, nodes):
        edges = self.graph.edges_iter(data=True)
        edges = {(u, v): a for u, v, a in edges if u != v}

        for i, v in enumerate(self.inputs):
            if v != START:
                n = (START, v)
                edges[n] = combine_dicts(edges.get(n, {}), {'inp_id': i})

        for i, u in enumerate(self.outputs):
            if u != END:
                n = (u, END)
                edges[n] = combine_dicts(edges.get(n, {}), {'out_id': i})

        for (u, v), a in edges.items():
            base = {'type': 'edge', 'dot_ids': (nodes[u].id, nodes[v].id)}
            a = combine_dicts(a, base=base)
            yield (u, v), self.folder_node(self, '{} --> {}'.format(u, v), a)

    def dot(self, context=None):
        context = context or {}
        kw = combine_nested_dicts(self.digraph, {
            'name': self.title,
            'body': {'label': '<%s>' % self.label_name}
        })
        kw['body'] = ['%s = %s' % (k, v) for k, v in sorted(kw['body'].items())]
        dot = DspPlot(self.sitemap, **kw)
        id_map = {}
        for node in self.nodes:
            id_map[node.node_id] = node.id
            dot.node(node.id, **node.dot(context))

        for edge in self.edges:
            dot.edge(*edge.attr['dot_ids'], **edge.dot(context))
        return dot

    def view(self, filepath, context=None):
        fpath, f = osp.splitext(filepath)
        dot = self.dot(context=context)
        dot.format = f[1:]
        fpath = dot.render(
            filename=tempfile.mktemp(dir=osp.dirname(filepath)), directory=None,
            cleanup=True
        )
        upath = uncpath(filepath)
        if osp.isfile(upath):
            os.remove(upath)
        os.rename(fpath, upath)
        return filepath,


class SiteIndex(SiteNode):
    ext='html'

    def __init__(self, sitemap, node_id='index'):
        super(SiteIndex, self).__init__(None, node_id, None)
        self.sitemap = sitemap
        dfl_folder = osp.join(
            pkg_resources.resource_filename(__name__, ''), 'static'
        )
        for default_file in glob.glob(dfl_folder + '/*'):
            self.extra_files.append(osp.relpath(default_file, dfl_folder))

    def render(self, context, *args, **kwargs):
        pkg_dir = pkg_resources.resource_filename(__name__, '')
        fpath = osp.join(pkg_dir, 'templates', self.filename)
        with open(fpath, 'r') as myfile:
            return jinja2_format(myfile.read(), {'sitemap': self.sitemap,
                                                 'context': context},
                                 loader=jinja2.PackageLoader(__name__))

    def view(self, filepath, *args, **kwargs):
        files = list(super(SiteIndex, self).view(filepath, *args, **kwargs))
        folder = osp.dirname(filepath)
        dfl_folder = osp.join(
            pkg_resources.resource_filename(__name__, ''), 'static'
        )
        for default_file in glob.glob(dfl_folder + '/*'):
            fpath = osp.join(folder, osp.relpath(default_file, dfl_folder))
            fpath = uncpath(fpath)
            if not osp.isfile(fpath):
                os.makedirs(osp.dirname(fpath), exist_ok=True)
                shutil.copy(default_file, fpath)
                files.append(fpath)
        return files


def run_server(app, options):
    app.run(**options)


def cleanup(files):
    while files:
        fpath = files.pop()
        try:
            os.remove(fpath)
        except FileNotFoundError:
            pass
        try:
            os.removedirs(osp.dirname(fpath))
        except OSError:  # The directory is not empty.
            pass
    return 'Cleaned up generated files by the server.'


def shutdown_server():
    func = flask.request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    return 'Server shutting down...'


def shutdown_site(url):
    import requests
    requests.post('%s/cleanup' % url)
    try:
        requests.post('%s/shutdown' % url)
    except requests.exceptions.ConnectionError:
        pass


class Site:
    def __init__(self, sitemap, host='localhost', port=0, **kwargs):
        self.sitemap = sitemap
        self.kwargs = kwargs
        self.host = host
        self.port = port
        self.shutdown = lambda: None

    def get_port(self, host=None, port=None, **kw):
        kw = kw.copy()
        kw['host'] = self.host = host or self.host
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((self.host, port or self.port))
        kw['port'] = self.port = sock.getsockname()[1]
        sock.close()
        return kw

    def _repr_html_(self):
        from IPython.display import IFrame
        self.run(host='localhost', port=0)
        return IFrame(self.url, width='100%', height=500)._repr_html_()

    @property
    def url(self):
        return 'http://{}:{}'.format(self.host, self.port)

    def run(self, **options):
        self.shutdown()
        import threading
        threading.Thread(
            target=run_server,
            args=(self.sitemap.app(**self.kwargs), self.get_port(**options))
        ).start()
        self.shutdown = weakref.finalize(self, shutdown_site, self.url)


class SiteMap(collections.OrderedDict):
    site_folder = SiteFolder
    site_node = SiteNode
    site_index = SiteIndex

    def __init__(self):
        super(SiteMap, self).__init__()
        self._nodes = []
        self.foldername = ''
        self.index = self.site_index(self)

    def __setitem__(self, key, value, *args, **kwargs):
        filenames = self.index_filenames()
        filenames += [v.foldername for k, v in self.items() if k is not key]
        value.foldername = valid_filename(key, filenames, ext='')
        super(SiteMap, self).__setitem__(key, value, *args, **kwargs)

    def _repr_svg_(self):
        dot = list(self)[-1].dot()
        return dot.pipe(format='svg').decode(dot._encoding)

    def index_filenames(self):
        filenames = []
        list(update_filenames(self.index, filenames))
        return filenames

    @property
    def nodes(self):
        return sorted(self._nodes, key=lambda x: x.title)

    def rules(self, depth=-1, index=True):
        filenames, rules = [], []
        rules.extend(self._rules(depth=depth, filenames=filenames))
        if index:
            rules.extend(list(update_filenames(self.index, filenames))[::-1])
        it = ((k, v.replace('\\', '/')) for k, v in reversed(rules))
        return collections.OrderedDict(it)

    def _rules(self, depth=-1, rule='', filenames=None):
        if self.foldername:
            rule = osp.join(rule, self.foldername)
        if filenames is None:
            filenames = []
        filenames += [v.foldername for k, v in self.items()]
        if depth != 0:
            depth -= 1
            for folder, smap in self.items():
                yield from smap._rules(rule=rule, depth=depth)
                for k, filename in update_filenames(folder, filenames):
                    yield k, osp.join(rule, filename)

        for node in self._nodes:
            for k, filename in update_filenames(node, filenames):
                yield k, osp.join(rule, filename)

    def add_item(self, item, workflow=False, **options):
        item = parent_func(item)
        if workflow:
            item = self.get_sol_from(item)
            dsp, graph = item.dsp, item.workflow
        else:
            dsp = self.get_dsp_from(item)
            graph = dsp.dmap

        folder = self.site_folder(item, dsp, graph, workflow=workflow, **options)
        folder.sitemap = smap = self[folder] = self.__class__()
        return smap, folder

    def add_items(self, item, workflow=False, depth=-1, **options):
        smap, folder = self.add_item(item, workflow=workflow, **options)
        if depth > 0:
            depth -= 1
        site_node, append = self.site_node, smap._nodes.append
        add_items = functools.partial(smap.add_items, workflow=workflow)
        for node in itertools.chain(folder.nodes, folder.edges):
            links, node_id = node._links, node.node_id
            only_site_node = depth == 0 or node.type == 'data'
            for k, item in node.items():
                try:
                    if only_site_node:
                        raise ValueError
                    link = add_items(item, depth=depth, name=node_id)
                except ValueError:  # item is not a dsp object.
                    link = site_node(folder, '%s-%s' % (node_id, k), item)
                    append(link)
                links[k] = link

        return folder

    @staticmethod
    def get_dsp_from(item):
        from .sol import Solution
        from .. import Dispatcher
        if isinstance(item, (Solution, SubDispatch)):
            return item.dsp
        elif isinstance(item, Dispatcher):
            return item
        raise ValueError('Type %s not supported.' % type(item).__name__)

    @staticmethod
    def get_sol_from(item):
        from .sol import Solution
        from .. import Dispatcher
        if isinstance(item, (Dispatcher, SubDispatch)):
            return item.solution
        elif isinstance(item, Solution):
            return item
        raise ValueError('Type %s not supported.' % type(item).__name__)

    def app(self, root_path=None, depth=-1, index=True, **kwargs):
        root_path = osp.abspath(root_path or tempfile.mktemp())
        app = flask.Flask(root_path, root_path=root_path, **kwargs)
        generated_files = []
        func = functools.partial(cleanup, generated_files)
        rule = '/cleanup'
        app.add_url_rule(rule, rule[1:], func, methods=['POST'])
        rule = '/shutdown'
        app.add_url_rule(rule, rule[1:], shutdown_server, methods=['POST'])
        context = self.rules(depth=depth, index=index)
        for (node, extra), filepath in context.items():
            func = functools.partial(
                site_view, app, node, filepath, context, generated_files
            )
            app.add_url_rule('/%s' % filepath, filepath, func)

        if context:
            app.add_url_rule('/', next(iter(context.values())))

        return app

    def site(self, root_path=None, depth=-1, index=True, view=False, **kw):
        site = Site(self, root_path=root_path, depth=depth, index=index, **kw)

        if view:
            site.run()
            DspPlot(None)._view(site.url, 'html')

        return site

    def render(self, depth=-1, directory='static', view=False, index=True):
        context = self.rules(depth=depth, index=index)
        for (node, extra), filepath in context.items():
            if not extra:
                node.view(osp.join(directory, filepath), context)
        fpath = osp.join(directory, next(iter(context.values()), ''))
        if view:
            DspPlot(None)._view(fpath, osp.splitext(fpath)[1][1:])
        return fpath
