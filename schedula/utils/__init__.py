#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# Copyright 2014-2016 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It contains utility classes and functions.

The utils module contains classes and functions of general utility used in
multiple places throughout `schedula`. Some of these are graph-specific
algorithms while others are more python tricks.

The utils module is composed of six submodules to make organization clearer.
The submodules are fairly different from each other, but the main uniting theme
is that all of these submodules are not specific to a particularly schedula
application.

.. note::
    The :mod:`~schedula.utils` module is composed of submodules that can be
    accessed separately. However, they are all also included in the base module.
    Thus, as an example, schedula.utils.gen.Token and schedula.utils.Token
    are different names for the same class (Token). The schedula.utils.Token
    usage is preferred as this allows the internal organization to be changed if
    it is deemed necessary.


Sub-Modules:

.. currentmodule:: schedula.utils

.. autosummary::
    :nosignatures:
    :toctree: utils/

    alg
    base
    cst
    des
    drw
    dsp
    exc
    exl
    gen
    io
    sol
    web
"""

__author__ = 'Vincenzo Arcidiacono'

from .cst import EMPTY, START, NONE, SINK, SELF, END, PLOT

from .dsp import (
    stlp, combine_dicts, bypass, summation, map_dict, map_list, selector,
    replicate_value, add_args, parse_args, stack_nested_keys, get_nested_dicts,
    are_in_nested_dicts, combine_nested_dicts, SubDispatch, parent_func,
    SubDispatchFunction, SubDispatchPipe
)

from .exc import DispatcherError, DispatcherAbort

from .exl import extract_dsp_from_excel

from .gen import counter, Token, pairwise

from .io import (
    save_dispatcher, load_dispatcher, save_default_values, load_default_values,
    save_map, load_map, open_file
)
