#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2014-2016 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl
"""
It contains a comprehensive list of all modules and classes within schedula.

Docstrings should provide sufficient understanding for any individual function.

Modules:

.. currentmodule:: schedula

.. autosummary::
    :nosignatures:
    :toctree: _build/schedula

    ~Dispatcher
    ~utils
"""

import threading
from .utils.cst import EMPTY, START, NONE, SINK, SELF, PLOT
from .utils.dsp import bypass, combine_dicts, selector, stlp, parent_func
from .utils.gen import counter
from .utils.base import Base


__all__ = ['Dispatcher']
__author__ = 'Vincenzo Arcidiacono'


class Dispatcher(Base):
    """
    It provides a data structure to process a complex system of functions.

    The scope of this data structure is to compute the shortest workflow between
    input and output data nodes.

    :ivar stopper:
        A semaphore (:class:`threading.Event`) to abort the dispatching.
        
    .. Tip::
        Remember to set :attr:`stopper` to `False` before dispatching ;-)

    A workflow is a sequence of function calls.

    \***************************************************************************

    **Example**:

    As an example, here is a system of equations:

    :math:`b - a = c`

    :math:`log(c) = d_{from-log}`

    :math:`d = (d_{from-log} + d_{initial-guess}) / 2`

    that will be solved assuming that :math:`a = 0`, :math:`b = 1`, and
    :math:`d_{initial-guess} = 4`.

    **Steps**

    Create an empty dispatcher::

        >>> dsp = Dispatcher(name='Dispatcher')

    Add data nodes to the dispatcher map::

        >>> dsp.add_data(data_id='a')
        'a'
        >>> dsp.add_data(data_id='c')
        'c'

    Add a data node with a default value to the dispatcher map::

        >>> dsp.add_data(data_id='b', default_value=1)
        'b'

    Add a function node::

        >>> def diff_function(a, b):
        ...     return b - a
        ...
        >>> dsp.add_function('diff_function', function=diff_function,
        ...                  inputs=['a', 'b'], outputs=['c'])
        'diff_function'

    Add a function node with domain::

        >>> from math import log
        ...
        >>> def log_domain(x):
        ...     return x > 0
        ...
        >>> dsp.add_function('log', function=log, inputs=['c'], outputs=['d'],
        ...                  input_domain=log_domain)
        'log'

    Add a data node with function estimation and callback function.

        - function estimation: estimate one unique output from multiple
          estimations.
        - callback function: is invoked after computing the output.

        >>> def average_fun(kwargs):
        ...     '''
        ...     Returns the average of node estimations.
        ...
        ...     :param kwargs:
        ...         Node estimations.
        ...     :type kwargs: dict
        ...
        ...     :return:
        ...         The average of node estimations.
        ...     :rtype: float
        ...     '''
        ...
        ...     x = kwargs.values()
        ...     return sum(x) / len(x)
        ...
        >>> def callback_fun(x):
        ...     print('(log(1) + 4) / 2 = %.1f' % x)
        ...
        >>> dsp.add_data(data_id='d', default_value=4, wait_inputs=True,
        ...              function=average_fun, callback=callback_fun)
        'd'

    .. dispatcher:: dsp
       :opt: graph_attr={'ratio': '1'}

        >>> dsp
        <...>

    Dispatch the function calls to achieve the desired output data node `d`:

    .. dispatcher:: outputs
       :opt: graph_attr={'ratio': '1'}
       :code:

        >>> outputs = dsp.dispatch(inputs={'a': 0}, outputs=['d'])
        (log(1) + 4) / 2 = 2.0
        >>> outputs
        Solution([('a', 0), ('b', 1), ('c', 1), ('d', 2.0)])
    """

    #: When True, the dispatching loop raise :exc:`DispatcherAbort` ASAP.
    stopper = threading.Event()

    def __init__(self, dmap=None, name='', default_values=None, raises=False,
                 description='', stopper=None):
        """
        Initializes the dispatcher.

        :param dmap:
            A directed graph that stores data & functions parameters.
        :type dmap: networkx.DiGraph, optional

        :param name:
            The dispatcher's name.
        :type name: str, optional

        :param default_values:
            Data node default values. These will be used as input if it is not
            specified as inputs in the ArciDispatch algorithm.
        :type default_values: dict[str, dict], optional

        :param raises:
            If True the dispatcher interrupt the dispatch when an error occur,
            otherwise it logs a warning.
        :type raises: bool, optional

        :param description:
            The dispatcher's description.
        :type description: str, optional

        :param stopper:
            A semaphore to abort the dispatching.
        :type stopper: threading.Event, optional
        """

        from networkx import DiGraph
        #: The directed graph that stores data & functions parameters.
        self.dmap = dmap or DiGraph()

        #: The dispatcher's name.
        self.name = name

        #: The dispatcher's description.
        self.__doc__ = description

        #: The function and data nodes of the dispatcher.
        self.nodes = self.dmap.node

        #: Data node default values. These will be used as input if it is not
        #: specified as inputs in the ArciDispatch algorithm.
        self.default_values = default_values or {}

        #: Weight tag.
        self.weight = 'weight'

        #: If True the dispatcher interrupt the dispatch when an error occur.
        self.raises = raises

        #: Stopper to abort the dispatcher execution.
        self.stopper = stopper or self.__class__.stopper

        from .utils.sol import Solution
        #: Last dispatch solution.
        self.solution = Solution(self)

        #: Counter to set the node index.
        self.counter = counter()

    def copy_structure(self, **kwargs):
        _map = {
            'description': '__doc__', 'name': 'name', 'stopper': 'stopper',
            'raises': 'raises'
        }
        base = {k: getattr(self, v) for k, v in _map.items()}
        obj = self.__class__(**combine_dicts(kwargs, base=base))
        obj.weight = self.weight
        return obj

    def add_data(self, data_id=None, default_value=EMPTY, initial_dist=0.0,
                 wait_inputs=False, wildcard=None, function=None, callback=None,
                 remote_links=None, description=None, filters=None, **kwargs):
        """
        Add a single data node to the dispatcher.

        :param data_id:
            Data node id. If None will be assigned automatically ('unknown<%d>')
            not in dmap.
        :type data_id: str, optional

        :param default_value:
            Data node default value. This will be used as input if it is not
            specified as inputs in the ArciDispatch algorithm.
        :type default_value: T, optional

        :param initial_dist:
            Initial distance in the ArciDispatch algorithm when the data node
            default value is used.
        :type initial_dist: float, int, optional

        :param wait_inputs:
            If True ArciDispatch algorithm stops on the node until it gets all
            input estimations.
        :type wait_inputs: bool, optional

        :param wildcard:
            If True, when the data node is used as input and target in the
            ArciDispatch algorithm, the input value will be used as input for
            the connected functions, but not as output.
        :type wildcard: bool, optional

        :param function:
            Data node estimation function.
            This can be any function that takes only one dictionary
            (key=function node id, value=estimation of data node) as input and
            return one value that is the estimation of the data node.
        :type function: function, optional

        :param callback:
            Callback function to be called after node estimation.
            This can be any function that takes only one argument that is the
            data node estimation output. It does not return anything.
        :type callback: function, optional

        :param remote_links:
            List of parent or child dispatcher nodes e.g., [[dsp_id, dsp], ...].
        :type remote_links: list[[str, Dispatcher]], optional

        :param description:
            Data node's description.
        :type description: str, optional

        :param filters:
            A list of functions that are invoked after the invocation of the
            main function.
        :type filters: list[function], optional

        :param kwargs:
            Set additional node attributes using key=value.
        :type kwargs: keyword arguments, optional

        :return:
            Data node id.
        :rtype: str

        .. seealso:: :func:`add_function`,  :func:`add_dispatcher`,
           :func:`add_from_lists`

        \***********************************************************************

        **Example**:

        .. testsetup::
            >>> dsp = Dispatcher(name='Dispatcher')

        Add a data to be estimated or a possible input data node::

            >>> dsp.add_data(data_id='a')
            'a'

        Add a data with a default value (i.e., input data node)::

            >>> dsp.add_data(data_id='b', default_value=1)
            'b'

        Create a data node with function estimation and a default value.

            - function estimation: estimate one unique output from multiple
              estimations.
            - default value: is a default estimation.

            >>> def min_fun(kwargs):
            ...     '''
            ...     Returns the minimum value of node estimations.
            ...
            ...     :param kwargs:
            ...         Node estimations.
            ...     :type kwargs: dict
            ...
            ...     :return:
            ...         The minimum value of node estimations.
            ...     :rtype: float
            ...     '''
            ...
            ...     return min(kwargs.values())
            ...
            >>> dsp.add_data(data_id='c', default_value=2, wait_inputs=True,
            ...              function=min_fun)
            'c'

        Create a data with an unknown id and return the generated id::

            >>> dsp.add_data()
            'unknown'
        """

        # Set special data nodes.
        if data_id is START:
            default_value, description = NONE, START.__doc__
        elif data_id is SINK:
            wait_inputs, function, description = True, bypass, SINK.__doc__
        elif data_id is SELF:
            default_value, description = self, SELF.__doc__
        elif data_id is PLOT:
            from .utils.drw import autoplot_callback, autoplot_function
            callback, description = callback or autoplot_callback, PLOT.__doc__
            function = function or autoplot_function

        # Base data node attributes.
        attr_dict = {
            'type': 'data',
            'wait_inputs': wait_inputs,
            'index': (self.counter(),)
        }

        if function is not None:  # Add function as node attribute.
            attr_dict['function'] = function

        if callback is not None:  # Add callback as node attribute.
            attr_dict['callback'] = callback

        if wildcard is not None:  # Add wildcard as node attribute.
            attr_dict['wildcard'] = wildcard

        if remote_links is not None:  # Add remote links.
            attr_dict['remote_links'] = remote_links

        if description is not None:  # Add description as node attribute.
            attr_dict['description'] = description

        if filters:  # Add filters as node attribute.
            attr_dict['filters'] = filters

        attr_dict.update(kwargs)  # Additional attributes.

        has_node = self.dmap.has_node  # Namespace shortcut for speed.

        if data_id is None:  # Search for an unused node id.
            from .utils.alg import get_unused_node_id
            data_id = get_unused_node_id(self.dmap)  # Get an unused node id.

        # Check if the node id exists as function.
        elif has_node(data_id) and self.dmap.node[data_id]['type'] != 'data':
            raise ValueError('Invalid data id: '
                             'override function {}'.format(data_id))

        # Add node to the dispatcher map.
        self.dmap.add_node(data_id, attr_dict=attr_dict)

        # Set default value.
        self.set_default_value(data_id, default_value, initial_dist)

        return data_id  # Return data node id.

    def add_function(self, function_id=None, function=None, inputs=None,
                     outputs=None, input_domain=None, weight=None,
                     inp_weight=None, out_weight=None, description=None,
                     filters=None, **kwargs):
        """
        Add a single function node to dispatcher.

        :param function_id:
            Function node id.
            If None will be assigned as <fun.__name__>.
        :type function_id: str, optional

        :param function:
            Data node estimation function.
        :type function: function, optional

        :param inputs:
            Ordered arguments (i.e., data node ids) needed by the function.
        :type inputs: list, optional

        :param outputs:
            Ordered results (i.e., data node ids) returned by the function.
        :type outputs: list, optional

        :param input_domain:
            A function that checks if input values satisfy the function domain.
            This can be any function that takes the same inputs of the function
            and returns True if input values satisfy the domain, otherwise
            False. In this case the dispatch algorithm doesn't pass on the node.
        :type input_domain: function, optional

        :param weight:
            Node weight. It is a weight coefficient that is used by the dispatch
            algorithm to estimate the minimum workflow.
        :type weight: float, int, optional

        :param inp_weight:
            Edge weights from data nodes to the function node.
            It is a dictionary (key=data node id) with the weight coefficients
            used by the dispatch algorithm to estimate the minimum workflow.
        :type inp_weight: dict[str, float | int], optional

        :param out_weight:
            Edge weights from the function node to data nodes.
            It is a dictionary (key=data node id) with the weight coefficients
            used by the dispatch algorithm to estimate the minimum workflow.
        :type out_weight: dict[str, float | int], optional

        :param description:
            Function node's description.
        :type description: str, optional

        :param filters:
            A list of functions that are invoked after the invocation of the
            main function.
        :type filters: list[function], optional

        :param kwargs:
            Set additional node attributes using key=value.
        :type kwargs: keyword arguments, optional

        :return:
            Function node id.
        :rtype: str

        .. seealso:: :func:`add_data`, :func:`add_dispatcher`,
           :func:`add_from_lists`

        \***********************************************************************

        **Example**:

        .. testsetup::
            >>> dsp = Dispatcher(name='Dispatcher')

        Add a function node::

            >>> def my_function(a, b):
            ...     c = a + b
            ...     d = a - b
            ...     return c, d
            ...
            >>> dsp.add_function(function=my_function, inputs=['a', 'b'],
            ...                  outputs=['c', 'd'])
            'my_function'

        Add a function node with domain::

            >>> from math import log
            >>> def my_log(a, b):
            ...     return log(b - a)
            ...
            >>> def my_domain(a, b):
            ...     return a < b
            ...
            >>> dsp.add_function(function=my_log, inputs=['a', 'b'],
            ...                  outputs=['e'], input_domain=my_domain)
            'my_log'
        """

        if inputs is None:  # Set a dummy input.
            if START not in self.nodes:
                self.add_data(START)

            inputs = [START]  # Update inputs.

        if outputs is None:  # Set a dummy output.
            if SINK not in self.nodes:
                self.add_data(SINK)

            outputs = [SINK]  # Update outputs.

        # Get parent function.
        func = parent_func(function)

        # Base function node attributes.
        attr_dict = {
            'type': 'function',
            'inputs': inputs,
            'outputs': outputs,
            'function': function,
            'wait_inputs': True,
            'index': (self.counter(),)
        }

        if input_domain:  # Add domain as node attribute.
            attr_dict['input_domain'] = input_domain

        if description is not None:  # Add description as node attribute.
            attr_dict['description'] = description

        if filters:  # Add filters as node attribute.
            attr_dict['filters'] = filters

        # Set function name.
        if function_id is None:
            try:  # Set function name.
                function_name = func.__name__
            except Exception as ex:
                raise ValueError('Invalid function id due to:\n{}'.format(ex))
        else:
            function_name = function_id

        from .utils.alg import get_unused_node_id
        # Get an unused node id.
        fun_id = get_unused_node_id(self.dmap, initial_guess=function_name)

        if weight is not None:  # Add weight as node attribute.
            attr_dict['weight'] = weight

        attr_dict.update(kwargs)  # Set additional attributes.

        # Add node to the dispatcher map.
        self.dmap.add_node(fun_id, attr_dict=attr_dict)

        from .utils.alg import add_func_edges  # Add input edges.
        n_data = add_func_edges(self, fun_id, inputs, inp_weight, True)

        # Add output edges.
        add_func_edges(self, fun_id, outputs, out_weight, False, n_data)

        return fun_id  # Return function node id.

    def add_dispatcher(self, dsp, inputs, outputs, dsp_id=None,
                       input_domain=None, weight=None, inp_weight=None,
                       description=None, include_defaults=False, **kwargs):
        """
        Add a single sub-dispatcher node to dispatcher.

        :param dsp:
            Child dispatcher that is added as sub-dispatcher node to the parent
            dispatcher.
        :type dsp: Dispatcher | dict[str, list]

        :param inputs:
            Inputs mapping. Data node ids from parent dispatcher to child
            sub-dispatcher.
        :type inputs: dict[str, str | list[str]]

        :param outputs:
            Outputs mapping. Data node ids from child sub-dispatcher to parent
            dispatcher.
        :type outputs: dict[str, str | list[str]]

        :param dsp_id:
            Sub-dispatcher node id.
            If None will be assigned as <dsp.name>.
        :type dsp_id: str, optional

        :param input_domain:
            A function that checks if input values satisfy the function domain.
            This can be any function that takes the a dictionary with the inputs
            of the sub-dispatcher node and returns True if input values satisfy
            the domain, otherwise False.

            .. note:: This function is invoked every time that a data node reach
               the sub-dispatcher node.
        :type input_domain: (dict) -> bool, optional

        :param weight:
            Node weight. It is a weight coefficient that is used by the dispatch
            algorithm to estimate the minimum workflow.
        :type weight: float, int, optional

        :param inp_weight:
            Edge weights from data nodes to the sub-dispatcher node.
            It is a dictionary (key=data node id) with the weight coefficients
            used by the dispatch algorithm to estimate the minimum workflow.
        :type inp_weight: dict[str, int | float], optional

        :param description:
            Sub-dispatcher node's description.
        :type description: str, optional

        :param include_defaults:
            If True the default values of the sub-dispatcher are added to the
            current dispatcher.
        :type include_defaults: bool, optional

        :param kwargs:
            Set additional node attributes using key=value.
        :type kwargs: keyword arguments, optional

        :return:
            Sub-dispatcher node id.
        :rtype: str

        .. seealso:: :func:`add_data`, :func:`add_function`,
           :func:`add_from_lists`

        \***********************************************************************

        **Example**:

        .. testsetup::
            >>> dsp = Dispatcher(name='Dispatcher')

        Create a sub-dispatcher::

            >>> sub_dsp = Dispatcher()
            >>> sub_dsp.add_function('max', max, ['a', 'b'], ['c'])
            'max'

        Add the sub-dispatcher to the parent dispatcher::

            >>> dsp.add_dispatcher(dsp_id='Sub-Dispatcher', dsp=sub_dsp,
            ...                    inputs={'A': 'a', 'B': 'b'},
            ...                    outputs={'c': 'C'})
            'Sub-Dispatcher'

        Add a sub-dispatcher node with domain::


            >>> def my_domain(kwargs):
            ...     return kwargs['C'] > 3
            ...
            >>> dsp.add_dispatcher(dsp_id='Sub-Dispatcher with domain',
            ...                    dsp=sub_dsp, inputs={'C': 'a', 'D': 'b'},
            ...                    outputs={'c': 'E'}, input_domain=my_domain)
            'Sub-Dispatcher with domain'
        """

        if not isinstance(dsp, Dispatcher):
            kw = dsp
            dsp = Dispatcher(name=dsp_id or 'unknown')
            dsp.add_from_lists(**kw)

        if not dsp_id:  # Get the dsp id.
            dsp_id = dsp.name or 'unknown'

        if description is None:  # Get description.
            description = dsp.__doc__ or None

        # Set zero as default input distances.
        _weight_from = dict.fromkeys(inputs.keys(), 0.0)
        _weight_from.update(inp_weight or {})

        from .utils.alg import _children  # Get children and parents nodes.
        children, parents = _children(inputs), _children(outputs)

        # Return dispatcher node id.
        dsp_id = self.add_function(
            dsp_id, dsp, sorted(inputs), sorted(parents), input_domain, weight,
            _weight_from, type='dispatcher', description=description,
            wait_inputs=False, **kwargs)

        # Set proper inputs.
        self.nodes[dsp_id]['inputs'] = inputs

        # Set proper outputs.
        self.nodes[dsp_id]['outputs'] = outputs

        remote_link = [dsp_id, self]  # Define the remote link.

        # Unlink node reference.
        for k in children.union(outputs).intersection(dsp.nodes):
            dsp.nodes[k] = dsp.nodes[k].copy()

        # Set remote link.
        for it, is_parent in [(children, True), (outputs, False)]:
            for k in it:
                dsp.set_data_remote_link(k, remote_link, is_parent=is_parent)

        # Import default values from sub-dispatcher.
        if include_defaults:
            dsp_dfl = dsp.default_values  # Namespace shortcut.

            remove = set()  # Set of nodes to remove after the import.

            # Set default values.
            for k, v in inputs.items():
                if isinstance(v, str):
                    if v in dsp_dfl:
                        self.set_default_value(k, **dsp_dfl.pop(v))
                else:
                    if v[0] in dsp_dfl:
                        self.set_default_value(k, **dsp_dfl.pop(v[0]))
                        remove.update(v[1:])

            # Remove default values.
            for k in remove:
                dsp_dfl.pop(k, None)

        return dsp_id  # Return sub-dispatcher node id.

    def add_from_lists(self, data_list=None, fun_list=None, dsp_list=None):
        """
        Add multiple function and data nodes to dispatcher.

        :param data_list:
            It is a list of data node kwargs to be loaded.
        :type data_list: list[dict], optional

        :param fun_list:
            It is a list of function node kwargs to be loaded.
        :type fun_list: list[dict], optional

        :param dsp_list:
            It is a list of sub-dispatcher node kwargs to be loaded.
        :type dsp_list: list[dict], optional

        :returns:

            - Data node ids.
            - Function node ids.
            - Sub-dispatcher node ids.
        :rtype: (list[str], list[str], list[str])

        .. seealso:: :func:`add_data`, :func:`add_function`,
           :func:`add_dispatcher`

        \***********************************************************************

        **Example**:

        .. testsetup::
            >>> dsp = Dispatcher(name='Dispatcher')

        Define a data list::

            >>> data_list = [
            ...     {'data_id': 'a'},
            ...     {'data_id': 'b'},
            ...     {'data_id': 'c'},
            ... ]

        Define a functions list::

            >>> def func(a, b):
            ...     return a + b
            ...
            >>> fun_list = [
            ...     {'function': func, 'inputs': ['a', 'b'], 'outputs': ['c']}
            ... ]

        Define a sub-dispatchers list::

            >>> sub_dsp = Dispatcher(name='Sub-dispatcher')
            >>> sub_dsp.add_function(function=func, inputs=['e', 'f'],
            ...                      outputs=['g'])
            'func'
            >>>
            >>> dsp_list = [
            ...     {'dsp_id': 'Sub', 'dsp': sub_dsp,
            ...      'inputs': {'a': 'e', 'b': 'f'}, 'outputs': {'g': 'c'}},
            ... ]

        Add function and data nodes to dispatcher::

            >>> dsp.add_from_lists(data_list, fun_list, dsp_list)
            (['a', 'b', 'c'], ['func'], ['Sub'])
        """

        if data_list:  # Add data nodes.
            data_ids = [self.add_data(**v) for v in data_list]  # Data ids.
        else:
            data_ids = []

        if fun_list:  # Add function nodes.
            fun_ids = [self.add_function(**v) for v in fun_list]  # Func ids.
        else:
            fun_ids = []

        if dsp_list:  # Add dispatcher nodes.
            dsp_ids = [self.add_dispatcher(**v) for v in dsp_list]  # Dsp ids.
        else:
            dsp_ids = []

        # Return data, function, and sub-dispatcher node ids.
        return data_ids, fun_ids, dsp_ids

    def set_default_value(self, data_id, value=EMPTY, initial_dist=0.0):
        """
        Set the default value of a data node in the dispatcher.

        :param data_id:
            Data node id.
        :type data_id: str

        :param value:
            Data node default value.

            .. note:: If `EMPTY` the previous default value is removed.
        :type value: T, optional

        :param initial_dist:
            Initial distance in the ArciDispatch algorithm when the data node
            default value is used.
        :type initial_dist: float, int, optional

        \***********************************************************************

        **Example**:

        A dispatcher with a data node named `a`::

            >>> dsp = Dispatcher(name='Dispatcher')
            ...
            >>> dsp.add_data(data_id='a')
            'a'

        Add a default value to `a` node::

            >>> dsp.set_default_value('a', value='value of the data')
            >>> list(sorted(dsp.default_values['a'].items()))
            [('initial_dist', 0.0), ('value', 'value of the data')]

        Remove the default value of `a` node::

            >>> dsp.set_default_value('a', value=EMPTY)
            >>> dsp.default_values
            {}
        """

        try:
            if self.dmap.node[data_id]['type'] == 'data':  # Check if data node.
                if value is EMPTY:
                    self.default_values.pop(data_id, None)  # Remove default.
                else:  # Add default.
                    self.default_values[data_id] = {
                        'value': value,
                        'initial_dist': initial_dist
                    }
                return
        except KeyError:
            pass
        raise ValueError('Input error: %s is not a data node' % data_id)

    def set_data_remote_link(self, data_id, remote_link=EMPTY, is_parent=True):
        """
        Set a remote link of a data node in the dispatcher.

        :param data_id:
            Data node id.
        :type data_id: str

        :param remote_link:
            Parent or child dispatcher and its node id (id, dsp).
        :type remote_link: [str, Dispatcher], optional

        :param is_parent:
            If True the link is inflow (parent), otherwise is outflow (child).
        :type is_parent: bool
        """

        nodes = self.nodes  # Namespace shortcut.

        if remote_link != EMPTY and data_id is SINK and data_id not in nodes:
            self.add_data(SINK)  # Add sink node.

        try:
            if self.dmap.node[data_id]['type'] == 'data':  # Check if data node.

                type = ['child', 'parent'][is_parent]  # Remote link type.

                if remote_link == EMPTY:
                    # Namespace shortcuts.
                    node = nodes.get(data_id, {})
                    rl = node.get('remote_links', [])

                    # Remove remote links.
                    for v in [v for v in rl if rl[1] == type]:
                        rl.remove(v)

                    # Remove remote link attribute.
                    if not rl:
                        node.pop('remote_links', None)
                else:
                    node = nodes[data_id]  # Namespace shortcuts.

                    rl = node['remote_links'] = node.get('remote_links', [])
                    if [remote_link, type] not in rl:  # Add remote link.
                        rl.append([remote_link, type])

                return
        except KeyError:
            pass
        raise ValueError('Input error: %s is not a data node' % data_id)

    def get_sub_dsp(self, nodes_bunch, edges_bunch=None):
        """
        Returns the sub-dispatcher induced by given node and edge bunches.

        The induced sub-dispatcher contains the available nodes in nodes_bunch
        and edges between those nodes, excluding those that are in edges_bunch.

        The available nodes are non isolated nodes and function nodes that have
        all inputs and at least one output.

        :param nodes_bunch:
            A container of node ids which will be iterated through once.
        :type nodes_bunch: list[str], iterable

        :param edges_bunch:
            A container of edge ids that will be removed.
        :type edges_bunch: list[(str, str)], iterable, optional

        :return:
            A dispatcher.
        :rtype: Dispatcher

        .. seealso:: :func:`get_sub_dsp_from_workflow`

        .. note::

            The sub-dispatcher edge or node attributes just point to the
            original dispatcher. So changes to the node or edge structure
            will not be reflected in the original dispatcher map while changes
            to the attributes will.

        \***********************************************************************

        **Example**:

        A dispatcher with a two functions `fun1` and `fun2`:

        .. dispatcher:: dsp
           :opt: graph_attr={'ratio': '1'}

            >>> dsp = Dispatcher(name='Dispatcher')
            >>> dsp.add_function(function_id='fun1', inputs=['a', 'b'],
            ...                   outputs=['c', 'd'])
            'fun1'
            >>> dsp.add_function(function_id='fun2', inputs=['a', 'd'],
            ...                   outputs=['c', 'e'])
            'fun2'

        Get the sub-dispatcher induced by given nodes bunch::

            >>> sub_dsp = dsp.get_sub_dsp(['a', 'c', 'd', 'e', 'fun2'])

        .. dispatcher:: sub_dsp
           :opt: graph_attr={'ratio': '1'}

            >>> sub_dsp.name = 'Sub-Dispatcher'
        """

        # Get real paths.
        nodes_bunch = [self.get_node(u)[1][0] for u in nodes_bunch]

        # Define an empty dispatcher.
        sub_dsp = self.copy_structure(dmap=self.dmap.subgraph(nodes_bunch))

        # Namespace shortcuts for speed.
        nodes, dmap_out_degree = sub_dsp.nodes, sub_dsp.dmap.out_degree
        dmap_dv, dmap_rm_edge = self.default_values, sub_dsp.dmap.remove_edge
        dmap_rm_node = sub_dsp.dmap.remove_node

        # Remove function nodes that has not whole inputs available.
        for u in nodes_bunch:
            n = nodes[u].get('inputs', None)  # Function inputs.
            # No all inputs
            if n is not None and not set(n).issubset(nodes_bunch):
                dmap_rm_node(u)  # Remove function node.

        # Remove edges that are not in edges_bunch.
        if edges_bunch is not None:
            for e in edges_bunch:  # Iterate sub-graph edges.
                dmap_rm_edge(*e)  # Remove edge.

        # Remove function node with no outputs.
        for u in [u for u, n in sub_dsp.dmap.nodes_iter(True)
                  if n['type'] == 'function']:

            if not dmap_out_degree(u):  # No outputs.
                dmap_rm_node(u)  # Remove function node.

        from networkx import isolates
        # Remove isolate nodes from sub-graph.
        sub_dsp.dmap.remove_nodes_from(isolates(sub_dsp.dmap))

        # Set default values.
        sub_dsp.default_values = {k: dmap_dv[k] for k in dmap_dv if k in nodes}

        return sub_dsp  # Return the sub-dispatcher.

    def get_sub_dsp_from_workflow(self, sources, graph=None, reverse=False,
                                  add_missing=False, check_inputs=True):
        """
        Returns the sub-dispatcher induced by the workflow from sources.

        The induced sub-dispatcher of the dsp contains the reachable nodes and
        edges evaluated with breadth-first-search on the workflow graph from
        source nodes.

        :param sources:
            Source nodes for the breadth-first-search.
            A container of nodes which will be iterated through once.
        :type sources: list[str], iterable

        :param graph:
            A directed graph where evaluate the breadth-first-search.
        :type graph: networkx.DiGraph, optional

        :param reverse:
            If True the workflow graph is assumed as reversed.
        :type reverse: bool, optional

        :param add_missing:
            If True, missing function' inputs are added to the sub-dispatcher.
        :type add_missing: bool, optional

        :param check_inputs:
            If True the missing function' inputs are not checked.
        :type check_inputs: bool, optional

        :return:
            A sub-dispatcher.
        :rtype: Dispatcher

        .. seealso:: :func:`get_sub_dsp`

        .. note::

            The sub-dispatcher edge or node attributes just point to the
            original dispatcher. So changes to the node or edge structure
            will not be reflected in the original dispatcher map while changes
            to the attributes will.

        \***********************************************************************

        **Example**:

        A dispatcher with a function `fun` and a node `a` with a default value:

        .. dispatcher:: dsp
           :opt: graph_attr={'ratio': '1'}

            >>> dsp = Dispatcher(name='Dispatcher')
            >>> dsp.add_data(data_id='a', default_value=1)
            'a'
            >>> dsp.add_function(function_id='fun1', inputs=['a', 'b'],
            ...                  outputs=['c', 'd'])
            'fun1'
            >>> dsp.add_function(function_id='fun2', inputs=['e'],
            ...                  outputs=['c'])
            'fun2'

        Dispatch with no calls in order to have a workflow::

            >>> o = dsp.dispatch(inputs=['a', 'b'], no_call=True)

        Get sub-dispatcher from workflow inputs `a` and `b`::

            >>> sub_dsp = dsp.get_sub_dsp_from_workflow(['a', 'b'])

        .. dispatcher:: sub_dsp
           :opt: graph_attr={'ratio': '1'}

            >>> sub_dsp.name = 'Sub-Dispatcher'

        Get sub-dispatcher from a workflow output `c`::

            >>> sub_dsp = dsp.get_sub_dsp_from_workflow(['c'], reverse=True)

        .. dispatcher:: sub_dsp
           :opt: graph_attr={'ratio': '1'}

            >>> sub_dsp.name = 'Sub-Dispatcher (reverse workflow)'
        """

        # Define an empty dispatcher map.
        sub_dsp = self.copy_structure()

        if not graph:  # Set default graph.
            graph = self.solution.workflow

        # Visited nodes used as queue.
        family = {}

        # Namespace shortcuts for speed.
        nodes, dmap_nodes = sub_dsp.dmap.node, self.dmap.node
        dlt_val, dsp_dlt_val = sub_dsp.default_values, self.default_values

        if not reverse:
            # Namespace shortcuts for speed.
            neighbors, dmap_succ = graph.neighbors_iter, self.dmap.succ
            succ, pred = sub_dsp.dmap.succ, sub_dsp.dmap.pred

            # noinspection PyUnusedLocal
            def _check_node_inputs(c, p):
                if c == START:
                    return True

                node_attr = dmap_nodes[c]

                if node_attr['type'] == 'function':
                    if set(node_attr['inputs']).issubset(family):
                        _set_node_attr(c)

                        # namespace shortcuts for speed
                        s_pred = pred[c]

                        for p in node_attr['inputs']:
                            # add attributes to both representations of edge
                            succ[p][c] = s_pred[p] = dmap_succ[p][c]
                    elif not check_inputs or add_missing:
                        if add_missing:
                            for p in node_attr['inputs'] and p not in family:
                                _set_node_attr(p, add2family=False)

                        _set_node_attr(c)

                        # namespace shortcuts for speed
                        s_pred = pred[c]

                        for p in set(node_attr['inputs']).intersection(family):
                            # add attributes to both representations of edge
                            succ[p][c] = s_pred[p] = dmap_succ[p][c]
                        return False

                    return True

                return False

        else:
            # Namespace shortcuts for speed.
            neighbors, dmap_succ = graph.predecessors_iter, self.dmap.pred
            pred, succ = sub_dsp.dmap.succ, sub_dsp.dmap.pred

            def _check_node_inputs(c, p):
                if c == START:
                    try:
                        node_attr = dmap_nodes[p]
                        return node_attr['type'] == 'data'
                    except KeyError:
                        return True
                return False
        from collections import deque
        queue = deque([])

        # Function to set node attributes.
        def _set_node_attr(n, add2family=True):
            # Set node attributes.
            nodes[n] = dmap_nodes[n]

            # Add node in the adjacency matrix.
            succ[n], pred[n] = ({}, {})

            if n in dsp_dlt_val:
                dlt_val[n] = dsp_dlt_val[n]  # Set the default value.

            if add2family:
                family[n] = neighbors(n)  # Append a new parent to the family.

                queue.append(n)

        # Set initial node attributes.
        for s in sources:
            if s in dmap_nodes and s in graph.node:
                _set_node_attr(s)

        # Start breadth-first-search.
        while queue:
            parent = queue.popleft()

            # Namespace shortcuts for speed.
            nbrs, dmap_nbrs = (succ[parent], dmap_succ[parent])

            # Iterate parent's children.
            for child in family[parent]:

                if _check_node_inputs(child, parent):
                    continue

                if child not in family:
                    _set_node_attr(child)  # Set node attributes.

                # Add attributes to both representations of edge: u-v and v-u.
                nbrs[child] = pred[child][parent] = dmap_nbrs[child]

        return sub_dsp  # Return the sub-dispatcher map.

    @property
    def data_nodes(self):
        """
        Returns all data nodes of the dispatcher.

        :return:
            All data nodes of the dispatcher.
        :rtype: dict[str, dict]
        """

        return {k: v for k, v in self.nodes.items() if v['type'] == 'data'}

    @property
    def function_nodes(self):
        """
        Returns all function nodes of the dispatcher.

        :return:
            All data function of the dispatcher.
        :rtype: dict[str, dict]
        """

        return {k: v for k, v in self.nodes.items() if v['type'] == 'function'}

    @property
    def sub_dsp_nodes(self):
        """
        Returns all sub-dispatcher nodes of the dispatcher.

        :return:
            All sub-dispatcher nodes of the dispatcher.
        :rtype: dict[str, dict]
        """

        return {k: v for k, v in self.nodes.items() if
                v['type'] == 'dispatcher'}

    def copy(self):
        """
        Returns a copy of the Dispatcher.

        :return:
            A copy of the Dispatcher.
        :rtype: Dispatcher

        Example::

            >>> dsp = Dispatcher()
            >>> dsp is dsp.copy()
            False
        """
        import copy
        return copy.deepcopy(self)  # Return the copy of the Dispatcher.

    def web(self, import_name=None, **options):
        """
        Creates a dispatcher Flask app.

        :param import_name:
            The name of the application package.
        :type import_name: str, optional

        :param options:
            Flask options.
        :type options: dict, optional

        :return:
            Flask app based on the given dispatcher.
        :rtype: flask.Flask
        """

        from .utils.web import create_flask_app
        return create_flask_app(self, import_name=import_name, **options)

    def dispatch(self, inputs=None, outputs=None, cutoff=None, inputs_dist=None,
                 wildcard=False, no_call=False, shrink=False,
                 rm_unused_nds=False, select_output_kw=None, _wait_in=None,
                 stopper=None):
        """
        Evaluates the minimum workflow and data outputs of the dispatcher
        model from given inputs.

        :param inputs:
            Input data values.
        :type inputs: dict[str, T], list[str], iterable, optional

        :param outputs:
            Ending data nodes.
        :type outputs: list[str], iterable, optional

        :param cutoff:
            Depth to stop the search.
        :type cutoff: float, int, optional

        :param inputs_dist:
            Initial distances of input data nodes.
        :type inputs_dist: dict[str, int | float], optional

        :param wildcard:
            If True, when the data node is used as input and target in the
            ArciDispatch algorithm, the input value will be used as input for
            the connected functions, but not as output.
        :type wildcard: bool, optional

        :param no_call:
            If True data node estimation function is not used and the input
            values are not used.
        :type no_call: bool, optional

        :param shrink:
            If True the dispatcher is shrink before the dispatch.

            .. seealso:: :func:`shrink_dsp`
        :type shrink: bool, optional

        :param rm_unused_nds:
            If True unused function and sub-dispatcher nodes are removed from
            workflow.
        :type rm_unused_nds: bool, optional

        :param select_output_kw:
            Kwargs of selector function to select specific outputs.
        :type select_output_kw: dict, optional

        :param _wait_in:
            Override wait inputs.
        :type _wait_in: dict, optional

        :param stopper:
            A semaphore to abort the dispatching.
        :type stopper: threading.Event, optional

        :return:
            Dictionary of estimated data node outputs.
        :rtype: schedula.utils.sol.Solution

        \***********************************************************************

        **Example**:

        A dispatcher with a function :math:`log(b - a)` and two data `a` and `b`
        with default values:

        .. dispatcher:: dsp
           :opt: graph_attr={'ratio': '1'}

            >>> dsp = Dispatcher(name='Dispatcher')
            >>> dsp.add_data(data_id='a', default_value=0)
            'a'
            >>> dsp.add_data(data_id='b', default_value=5)
            'b'
            >>> dsp.add_data(data_id='d', default_value=1)
            'd'
            >>> from math import log
            >>> def my_log(a, b):
            ...     return log(b - a)
            >>> def my_domain(a, b):
            ...     return a < b
            >>> dsp.add_function('log(b - a)', function=my_log,
            ...                  inputs=['c', 'd'],
            ...                  outputs=['e'], input_domain=my_domain)
            'log(b - a)'
            >>> dsp.add_function('min', function=min, inputs=['a', 'b'],
            ...                  outputs=['c'])
            'min'

        Dispatch without inputs. The default values are used as inputs:

        .. dispatcher:: outputs
           :opt: graph_attr={'ratio': '1'}
           :code:

            >>> outputs = dsp.dispatch()
            >>> outputs
            Solution([('a', 0), ('b', 5), ('d', 1), ('c', 0), ('e', 0.0)])

        Dispatch until data node `c` is estimated:

        .. dispatcher:: outputs
           :opt: graph_attr={'ratio': '1'}
           :code:

            >>> outputs = dsp.dispatch(outputs=['c'])
            >>> outputs
            Solution([('a', 0), ('b', 5), ('c', 0)])

        Dispatch with one inputs. The default value of `a` is not used as
        inputs:

        .. dispatcher:: outputs
           :opt: graph_attr={'ratio': '1'}
           :code:

            >>> outputs = dsp.dispatch(inputs={'a': 3})
            >>> outputs
            Solution([('a', 3), ('b', 5), ('d', 1), ('c', 3)])
        """

        dsp = self

        if not no_call:
            if shrink:  # Pre shrink.
                dsp = self.shrink_dsp(
                    inputs, outputs, cutoff, inputs_dist, wildcard
                )
            elif outputs:
                dsp = self.shrink_dsp(outputs=outputs)

        # Initialize.
        self.solution = sol = self.solution.__class__(
            dsp, inputs, outputs, wildcard, cutoff, inputs_dist, no_call,
            rm_unused_nds, _wait_in, stopper=stopper
        )

        # Dispatch.
        sol.run()

        if select_output_kw:
            return selector(dictionary=sol, **select_output_kw)

        # Return the evaluated data outputs.
        return sol

    def shrink_dsp(self, inputs=None, outputs=None, cutoff=None,
                   inputs_dist=None, wildcard=True):
        """
        Returns a reduced dispatcher.

        :param inputs:
            Input data nodes.
        :type inputs: list[str], iterable, optional

        :param outputs:
            Ending data nodes.
        :type outputs: list[str], iterable, optional

        :param cutoff:
            Depth to stop the search.
        :type cutoff: float, int, optional

        :param inputs_dist:
            Initial distances of input data nodes.
        :type inputs_dist: dict[str, int | float], optional

        :param wildcard:
            If True, when the data node is used as input and target in the
            ArciDispatch algorithm, the input value will be used as input for
            the connected functions, but not as output.
        :type wildcard: bool, optional

        :return:
            A sub-dispatcher.
        :rtype: Dispatcher

        .. seealso:: :func:`dispatch`

        \***********************************************************************

        **Example**:

        A dispatcher like this:

        .. dispatcher:: dsp
           :opt: graph_attr={'ratio': '1'}

            >>> dsp = Dispatcher(name='Dispatcher')
            >>> functions = [
            ...     {
            ...         'function_id': 'fun1',
            ...         'inputs': ['a', 'b'],
            ...         'outputs': ['c']
            ...     },
            ...     {
            ...         'function_id': 'fun2',
            ...         'inputs': ['b', 'd'],
            ...         'outputs': ['e']
            ...     },
            ...     {
            ...         'function_id': 'fun3',
            ...         'function': min,
            ...         'inputs': ['d', 'f'],
            ...         'outputs': ['g']
            ...     },
            ...     {
            ...         'function_id': 'fun4',
            ...         'function': max,
            ...         'inputs': ['a', 'b'],
            ...         'outputs': ['g']
            ...     },
            ...     {
            ...         'function_id': 'fun5',
            ...         'function': max,
            ...         'inputs': ['d', 'e'],
            ...         'outputs': ['c', 'f']
            ...     },
            ... ]
            >>> dsp.add_from_lists(fun_list=functions)
            ([], [...])

        Get the sub-dispatcher induced by dispatching with no calls from inputs
        `a`, `b`, and `c` to outputs `c`, `e`, and `f`::

            >>> shrink_dsp = dsp.shrink_dsp(inputs=['a', 'b', 'd'],
            ...                             outputs=['c', 'f'])

        .. dispatcher:: shrink_dsp
           :opt: graph_attr={'ratio': '1'}

            >>> shrink_dsp.name = 'Sub-Dispatcher'
        """

        bfs = None
        if inputs:
            # Get all data nodes no wait inputs.
            wait_in = self._get_wait_in(flag=False)

            # Evaluate the workflow graph without invoking functions.
            o = self.dispatch(
                inputs, outputs, cutoff, inputs_dist, wildcard, True, False,
                True, _wait_in=wait_in
            )

            data_nodes = self.data_nodes  # Get data nodes.

            from .utils.alg import _union_workflow, _convert_bfs
            bfs = _union_workflow(o)  # bfg edges.

            # Set minimum initial distances.
            if inputs_dist:
                inputs_dist = combine_dicts(o.dist, inputs_dist)
            else:
                inputs_dist = o.dist

            # Set data nodes to wait inputs.
            wait_in = self._get_wait_in(flag=True)

            while True:  # Start shrinking loop.
                # Evaluate the workflow graph without invoking functions.
                o = self.dispatch(
                    inputs, outputs, cutoff, inputs_dist, wildcard, True, False,
                    False, _wait_in=wait_in
                )

                _union_workflow(o, bfs=bfs)  # Update bfs.

                n_d, status = o._remove_wait_in()  # Remove wait input flags.

                if not status:
                    break  # Stop iteration.

                # Update inputs.
                inputs = n_d.intersection(data_nodes).union(inputs)

            # Update outputs and convert bfs in DiGraphs.
            outputs, bfs = outputs or o, _convert_bfs(bfs)

        elif not outputs:
            return self.copy_structure()  # Empty Dispatcher.

        # Get sub dispatcher breadth-first-search graph.
        dsp = self._get_dsp_from_bfs(outputs, bfs_graphs=bfs)

        return dsp  # Return the shrink sub dispatcher.

    def _get_dsp_from_bfs(self, outputs, bfs_graphs=None, _update_links=True):
        """
        Returns the sub-dispatcher induced by the workflow from outputs.

        :param outputs:
            Ending data nodes.
        :type outputs: list[str], iterable, optional

        :param bfs_graphs:
            A dictionary with directed graphs where evaluate the
            breadth-first-search.
        :type bfs_graphs: dict[str | Token, networkx.DiGraph | dict], optional

        :return:
            A sub-dispatcher
        :rtype: Dispatcher
        """

        bfs = bfs_graphs[NONE] if bfs_graphs is not None else self.dmap

        # Get sub dispatcher breadth-first-search graph.
        dsp = self.get_sub_dsp_from_workflow(outputs, bfs, True)

        # Namespace shortcuts.
        in_e, out_e = dsp.dmap.in_edges, dsp.dmap.out_edges
        succ, nodes = dsp.dmap.succ, dsp.nodes
        rm_edges = dsp.dmap.remove_edges_from
        from .utils.alg import _children

        for n in dsp.sub_dsp_nodes:
            a = nodes[n] = nodes[n].copy()
            bfs = bfs_graphs[n] if bfs_graphs is not None else None

            o = succ[n]
            o = {k for k, v in a['outputs'].items()
                 if any(i in o for i in stlp(v))}

            if 'input_domain' in a:
                o = o.union(set(_children(a['inputs'])))

            d = a['function'] = a['function']._get_dsp_from_bfs(o, bfs, False)
            from .utils.alg import _update_io_attr_sub_dsp
            _update_io_attr_sub_dsp(d, a)

            # Update sub-dispatcher edges.
            o = set(out_e(n)) - {(n, u) for u in _children(a['outputs'])}
            i = set(in_e(n)) - {(u, n) for u in a['inputs']}
            rm_edges(i.union(o))  # Remove unreachable nodes.

        if _update_links:
            from .utils.alg import _update_remote_links, remove_links
            _update_remote_links(dsp, self)  # Update remote links.

            remove_links(dsp)  # Remove unused links.

        return dsp

    def _edge_length(self, edge, node_out):
        """
        Returns the edge length.

        The edge length is edge weight + destination node weight.

        :param edge:
            Edge attributes.
        :type edge: dict[str, int | float]

        :param node_out:
            Node attributes.
        :type node_out: dict[str, int | float]

        :return:
            Edge length.
        :rtype: float, int
        """

        weight = self.weight  # Namespace shortcut.

        return edge.get(weight, 1) + node_out.get(weight, 0)  # Return length.

    def _get_wait_in(self, flag=True, all_domain=True):
        """
        Set `wait_inputs` flags for data nodes that:

            - are estimated from functions with a domain function, and
            - are waiting inputs.

        :param flag:
            Value to be set. If None `wait_inputs` are just cleaned.
        :type flag: bool, None, optional

        :param all_domain:
            Set `wait_inputs` flags for data nodes that are estimated from
            functions with a domain function.
        :type all_domain: bool, optional
        """

        wait_in = {}

        if flag is None:  # No set.
            for a in self.sub_dsp_nodes.values():
                if 'function' in a:
                    a['function']._get_wait_in(flag=flag)
            return

        for n, a in self.data_nodes.items():
            if n is not SINK and a['wait_inputs']:
                wait_in[n] = flag

        if all_domain:
            for a in self.function_nodes.values():
                if 'input_domain' in a:
                    wait_in.update(dict.fromkeys(a['outputs'], flag))

            for n, a in self.sub_dsp_nodes.items():
                if 'function' in a:
                    dsp = a['function']
                    wait_in[dsp] = w = dsp._get_wait_in(flag=flag)
                    if 'input_domain' not in a:
                        o = a['outputs']
                        w = [o[k] for k in set(o).intersection(w)]
                        wait_in.update(dict.fromkeys(w, flag))

                if 'input_domain' in a:
                    wait_in[n] = flag
                    wait_in.update(dict.fromkeys(a['outputs'].values(), flag))

        return wait_in
