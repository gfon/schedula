# -*- coding: utf-8 -*-
#
# Copyright 2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

"""
It contains functions to predict the CO2 emissions.
"""

import copy
import functools
import itertools
import lmfit
import numpy as np
import scipy.integrate as sci_itg
import scipy.stats as sci_sta
import sklearn.metrics as sk_met
import co2mpas.dispatcher.utils as dsp_utl
import co2mpas.dispatcher as dsp
import co2mpas.utils as co2_utl
import co2mpas.model.physical.defaults as defaults


def default_fuel_density(fuel_type):
    """
    Returns the default fuel density [g/l].

    :param fuel_type:
        Fuel type (diesel, gasoline, LPG, NG, ethanol, biodiesel).
    :type fuel_type: str

    :return:
        Fuel density [g/l].
    :rtype: float
    """

    return defaults.dfl.functions.default_fuel_density.FUEL_DENSITY[fuel_type]


def default_fuel_carbon_content(fuel_type):
    """
    Returns the default fuel carbon content [CO2g/g].

    :param fuel_type:
        Fuel type (diesel, gasoline, LPG, NG, ethanol, biodiesel).
    :type fuel_type: str

    :return:
        Fuel carbon content [CO2g/g].
    :rtype: float
    """
    CC = defaults.dfl.functions.default_fuel_carbon_content.CARBON_CONTENT
    return CC[fuel_type]


def default_engine_fuel_lower_heating_value(fuel_type):
    """
    Returns the default fuel lower heating value [kJ/kg].

    :param fuel_type:
        Fuel type (diesel, gasoline, LPG, NG, ethanol, biodiesel).
    :type fuel_type: str

    :return:
        Fuel lower heating value [kJ/kg].
    :rtype: float
    """
    LHV = defaults.dfl.functions.default_fuel_lower_heating_value.LHV
    return LHV[fuel_type]


def calculate_fuel_carbon_content(fuel_carbon_content_percentage):
    """
    Calculates the fuel carbon content as CO2g/g.

    :param fuel_carbon_content_percentage:
        Fuel carbon content [%].
    :type fuel_carbon_content_percentage: float

    :return:
        Fuel carbon content [CO2g/g].
    :rtype: float
    """
    return fuel_carbon_content_percentage / 100.0 * 44.0 / 12.0


def calculate_fuel_carbon_content_percentage(fuel_carbon_content):
    """
    Calculates the fuel carbon content as %.

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :return:
        Fuel carbon content [%].
    :rtype: float
    """

    return fuel_carbon_content / calculate_fuel_carbon_content(1.0)


def calculate_normalized_engine_coolant_temperatures(
        engine_coolant_temperatures, temperature_target):
    """
    Calculates the normalized engine coolant temperatures [-].

    ..note::
        Engine coolant temperatures are first converted in kelvin and then
        normalized. The results is between ``[0, 1]``.

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param temperature_target:
        Normalization temperature [°C].
    :type temperature_target: float

    :return:
        Normalized engine coolant temperature [-].
    :rtype: numpy.array
    """

    i = np.searchsorted(engine_coolant_temperatures, (temperature_target,))[0]
    # Only flatten-out hot-part if `max-theta` is above `trg`.
    T = np.ones_like(engine_coolant_temperatures, dtype=float)
    T[:i] = engine_coolant_temperatures[:i] + 273.0
    T[:i] /= temperature_target + 273.0

    return T


def calculate_brake_mean_effective_pressures(
        engine_speeds_out, engine_powers_out, engine_capacity,
        min_engine_on_speed):
    """
    Calculates engine brake mean effective pressure [bar].

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: numpy.array

    :param engine_powers_out:
        Engine power vector [kW].
    :type engine_powers_out: numpy.array

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param min_engine_on_speed:
        Minimum engine speed to consider the engine to be on [RPM].
    :type min_engine_on_speed: float

    :return:
        Engine brake mean effective pressure vector [bar].
    :rtype: numpy.array
    """

    b = engine_speeds_out > min_engine_on_speed

    p = np.zeros_like(engine_powers_out)
    p[b] = engine_powers_out[b] / engine_speeds_out[b]
    p[b] *= 1200000.0 / engine_capacity

    return np.nan_to_num(p)


class IdleFuelConsumptionModel(object):
    def __init__(self, fc=None):
        self.fc = fc

    def fit(self, idle_engine_speed, engine_capacity, engine_stroke, lhv,
            fmep_model):
        idle = idle_engine_speed[0]
        from . import calculate_mean_piston_speeds
        self.n_s = calculate_mean_piston_speeds(idle, engine_stroke)
        self.c = idle * (engine_capacity / (lhv * 1200))
        self.fmep_model = fmep_model
        return self

    def consumption(self, params=None, ac_phases=None):
        if self.fc is not None:
            return self.fc, 1

        if isinstance(params, lmfit.Parameters):
            params = params.valuesdict()
        fc, _, ac = self.fmep_model(params, self.n_s, 0, ac_phases=ac_phases)
        fc *= self.c  # [g/sec]
        return fc, ac


def define_idle_fuel_consumption_model(
        idle_engine_speed, engine_capacity, engine_stroke,
        engine_fuel_lower_heating_value, fmep_model,
        idle_fuel_consumption=None):

    model = IdleFuelConsumptionModel(idle_fuel_consumption).fit(
        idle_engine_speed, engine_capacity, engine_stroke,
        engine_fuel_lower_heating_value, fmep_model
    )

    return model


def calculate_engine_idle_fuel_consumption(
        idle_fuel_consumption_model, params=None):
    """
    Calculates fuel consumption at hot idle engine speed [g/s].

    :param engine_idle_fuel_consumption:
        Fuel consumption at hot idle engine speed [g/s].
    :type engine_idle_fuel_consumption: float, optional

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: dict

    :return:
        Fuel consumption at hot idle engine speed [g/s].
    :rtype: float
    """

    return idle_fuel_consumption_model.consumption(params)[0]


class FMEP(object):
    def __init__(self, full_bmep_curve, active_cylinder_ratios=(1.0,),
                 has_cylinder_deactivation=False):
        self.acr = set(active_cylinder_ratios)
        self.fbc = full_bmep_curve
        self.has_cylinder_deactivation = has_cylinder_deactivation

    def __call__(self, params, n_speeds, n_powers, n_temperatures,
                 ac_phases=None):
        A, B, C = _fuel_ABC(
            n_speeds, n_powers, n_temperatures, **params
        )

        if 'acr' not in params:

            a = max(self.acr)
            acr = self.acr - {a}
            if a != 1:
                C += n_powers
                fmep, v = _calculate_fc(a * a * A, a * B, C - n_powers / a)
            else:
                fmep, v = _calculate_fc(A, B, C)
                C += n_powers

            if not (self.has_cylinder_deactivation and acr):
                ac = np.ones_like(fmep) * a
            else:
                def g(x, b):
                    try:
                        return x[b]
                    except (TypeError, IndexError):
                        return x

                b = n_temperatures == 1
                if ac_phases is not None:
                    b &= np.array(ac_phases, dtype=bool)
                    fmep *= np.ones_like(ac_phases, dtype=float)

                ac = np.ones_like(fmep)
                c_ratio = n_powers / (self.fbc(n_speeds) * 0.9)

                v *= np.ones_like(fmep)
                for a in acr:
                    n = b & (c_ratio <= a)
                    ABC = a * a * g(A, n), a * g(B, n), g(C, n) - g(n_powers, n) / a
                    r = _calculate_fc(*ABC)
                    try:
                        i = n[n] = fmep[n] > r[0]
                        fmep[n], v[n], ac[n] = g(r[0], i), g(r[1], i), a
                    except TypeError:
                        if n and fmep > r[0]:
                            (fmep, v), ac = r, a

        else:
            fmep, v = _calculate_fc(A, B, C)
            ac = np.ones_like(fmep) * params.get('acr', 1)

        return fmep, v, ac


def define_fmep_model(
        full_bmep_curve, active_cylinder_ratios, has_cylinder_deactivation):

    model = FMEP(full_bmep_curve, active_cylinder_ratios,
                 has_cylinder_deactivation)

    return model


# noinspection PyUnusedLocal
def _fuel_ABC(
        n_speeds, n_powers, n_temperatures,
        a2=0, b2=0, a=0, b=0, c=0, t=0, l=0, l2=0, acr=1, **kw):
    acr2 = acr ** 2
    A = acr2 * a2 + (acr2 * b2) * n_speeds
    B = acr * a + (acr * b + (acr * c) * n_speeds) * n_speeds
    C = np.power(n_temperatures, -t) * (l + l2 * n_speeds ** 2)
    C -= n_powers / acr

    return A, B, C


def _calculate_fc(A, B, C):
    b = np.array(A, dtype=bool)
    if b.all():
        v = np.sqrt(np.abs(B ** 2 - 4.0 * A * C))
        return (-B + v) / (2 * A), v
    elif np.logical_not(b).all():
        return -C / B, B
    else:
        fc, v = np.zeros_like(C), np.zeros_like(C)
        fc[b], v[b] = _calculate_fc(A[b], B[b], C[b])
        b = np.logical_not(b)
        fc[b], v[b] = _calculate_fc(A[b], B[b], C[b])
        return fc, v


def calculate_p0(
        params, engine_capacity, engine_stroke, idle_engine_speed_median,
        engine_fuel_lower_heating_value, fmep_model, ac_phases=None):
    """
    Calculates the engine power threshold limit [kW].

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: dict

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :param idle_engine_speed_median:
        Engine speed idle median [RPM].
    :type idle_engine_speed_median: float

    :param engine_fuel_lower_heating_value:
        Fuel lower heating value [kJ/kg].
    :type engine_fuel_lower_heating_value: float

    :return:
        Engine power threshold limit [kW].
    :rtype: float
    """

    engine_cm_idle = idle_engine_speed_median * engine_stroke / 30000.0

    lhv = engine_fuel_lower_heating_value

    wfb_idle, wfa_idle = fmep_model(params, engine_cm_idle, 0, 1, ac_phases)[:2]
    wfa_idle = (3600000.0 / lhv) / wfa_idle
    wfb_idle *= (3.0 * engine_capacity / lhv * idle_engine_speed_median)

    return -wfb_idle / wfa_idle


def calculate_co2_emissions(
        engine_speeds_out, engine_powers_out, mean_piston_speeds,
        brake_mean_effective_pressures, engine_coolant_temperatures, on_engine,
        engine_fuel_lower_heating_value, idle_engine_speed, engine_stroke,
        engine_capacity, idle_fuel_consumption_model, fuel_carbon_content,
        min_engine_on_speed, tau_function, fmep_model, params, sub_values=None):
    """
    Calculates CO2 emissions [CO2g/s].

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: numpy.array

    :param engine_powers_out:
        Engine power vector [kW].
    :type engine_powers_out: numpy.array

    :param mean_piston_speeds:
        Mean piston speed vector [m/s].
    :type mean_piston_speeds: numpy.array

    :param brake_mean_effective_pressures:
        Engine brake mean effective pressure vector [bar].
    :type brake_mean_effective_pressures: numpy.array

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: numpy.array

    :param engine_fuel_lower_heating_value:
        Fuel lower heating value [kJ/kg].
    :type engine_fuel_lower_heating_value: float

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param idle_fuel_consumption_model:
        Model of fuel consumption at hot idle engine speed.
    :type idle_fuel_consumption_model: IdleFuelConsumptionModel

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :param min_engine_on_speed:
        Minimum engine speed to consider the engine to be on [RPM].
    :type min_engine_on_speed: float

    :param tau_function:
        Tau-function of the extended Willans curve.
    :type tau_function: function

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: lmfit.Parameters

    :param sub_values:
        Boolean vector.
    :type sub_values: numpy.array, optional

    :return:
        CO2 emissions vector [CO2g/s].
    :rtype: numpy.array
    """

    p = params.valuesdict()

    if sub_values is None:
        sub_values = np.ones_like(mean_piston_speeds, dtype=bool)

    # namespace shortcuts
    n_speeds = mean_piston_speeds[sub_values]
    n_powers = brake_mean_effective_pressures[sub_values]
    lhv = engine_fuel_lower_heating_value
    e_speeds = engine_speeds_out[sub_values]
    e_powers = engine_powers_out[sub_values]
    e_temp = engine_coolant_temperatures[sub_values]

    fc, ac  = np.zeros_like(e_powers), np.ones_like(e_powers)

    # Idle fc correction for temperature
    b = (e_speeds < idle_engine_speed[0] + min_engine_on_speed)

    if p['t0'] == 0 and p['t1'] == 0:
        ac_phases = n_temp = np.ones_like(e_powers)
        fc[b], ac[b] = idle_fuel_consumption_model.consumption(p)
        b = np.logical_not(b)
    else:
        p['t'] = tau_function(p['t0'], p['t1'], e_temp)
        func = calculate_normalized_engine_coolant_temperatures
        n_temp = func(e_temp, p['trg'])
        ac_phases = n_temp == 1
        idle_fc, ac[b] = idle_fuel_consumption_model.consumption(p, ac_phases)
        fc[b] = idle_fc * np.power(n_temp[b], -p['t'][b])
        b = np.logical_not(b)
        p['t'] = p['t'][b]

    fc[b], _, ac[b] = fmep_model(p, n_speeds[b], n_powers[b], n_temp[b])
    fc[b] *= e_speeds[b] * (engine_capacity / (lhv * 1200))  # [g/sec]
    p['t'] = 0

    par = defaults.dfl.functions.calculate_co2_emissions
    idle_cutoff = idle_engine_speed[0] * par.cutoff_idle_ratio
    ec_p0 = calculate_p0(
        p, engine_capacity, engine_stroke, idle_cutoff, lhv, fmep_model,
        ac_phases
    )
    b = (e_powers <= ec_p0) & (e_speeds > idle_cutoff)
    fc[b | (e_speeds < min_engine_on_speed) | (fc < 0)] = 0

    co2 = fc * fuel_carbon_content

    return np.nan_to_num(co2), ac


def define_co2_emissions_model(
        engine_speeds_out, engine_powers_out, mean_piston_speeds,
        brake_mean_effective_pressures, engine_coolant_temperatures, on_engine,
        engine_fuel_lower_heating_value, idle_engine_speed, engine_stroke,
        engine_capacity, idle_fuel_consumption_model, fuel_carbon_content,
        min_engine_on_speed, tau_function, fmep_model):
    """
    Returns CO2 emissions model (see :func:`calculate_co2_emissions`).

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: numpy.array

    :param engine_powers_out:
        Engine power vector [kW].
    :type engine_powers_out: numpy.array

    :param mean_piston_speeds:
        Mean piston speed vector [m/s].
    :type mean_piston_speeds: numpy.array

    :param brake_mean_effective_pressures:
        Engine brake mean effective pressure vector [bar].
    :type brake_mean_effective_pressures: numpy.array

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: numpy.array

    :param engine_fuel_lower_heating_value:
        Fuel lower heating value [kJ/kg].
    :type engine_fuel_lower_heating_value: float

    :param idle_engine_speed:
        Engine speed idle median and std [RPM].
    :type idle_engine_speed: (float, float)

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :param min_engine_on_speed:
        Minimum engine speed to consider the engine to be on [RPM].
    :type min_engine_on_speed: float

    :param tau_function:
        Tau-function of the extended Willans curve.
    :type tau_function: function

    :return:
        CO2 emissions model (co2_emissions = models(params)).
    :rtype: function
    """

    model = functools.partial(
        calculate_co2_emissions, engine_speeds_out, engine_powers_out,
        mean_piston_speeds, brake_mean_effective_pressures,
        engine_coolant_temperatures, on_engine, engine_fuel_lower_heating_value,
        idle_engine_speed, engine_stroke, engine_capacity,
        idle_fuel_consumption_model, fuel_carbon_content, min_engine_on_speed,
        tau_function, fmep_model
    )

    return model


def select_phases_integration_times(cycle_type):
    """
    Selects the cycle phases integration times [s].

    :param cycle_type:
        Cycle type (WLTP or NEDC).
    :type cycle_type: str

    :return:
        Cycle phases integration times [s].
    :rtype: tuple
    """
    v = defaults.dfl.functions.select_phases_integration_times.INTEGRATION_TIMES
    return tuple(dsp_utl.pairwise(v[cycle_type.upper()]))


def calculate_phases_distances(times, phases_integration_times, velocities):
    """
    Calculates cycle phases distances [km].

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param velocities:
        Velocity vector [km/h].
    :type velocities: numpy.array

    :return:
        Cycle phases distances [km].
    :rtype: numpy.array
    """

    vel = velocities / 3600.0

    return calculate_cumulative_co2(times, phases_integration_times, vel)


def calculate_cumulative_co2(
        times, phases_integration_times, co2_emissions,
        phases_distances=1.0):
    """
    Calculates CO2 emission or cumulative CO2 of cycle phases [CO2g/km or CO2g].

    If phases_distances is not given the result is the cumulative CO2 of cycle
    phases [CO2g] otherwise it is CO2 emission of cycle phases [CO2g/km].

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param co2_emissions:
        CO2 instantaneous emissions vector [CO2g/s].
    :type co2_emissions: numpy.array

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array | float, optional

    :return:
        CO2 emission or cumulative CO2 of cycle phases [CO2g/km or CO2g].
    :rtype: numpy.array
    """

    co2 = []

    for p in phases_integration_times:
        i, j = np.searchsorted(times, p)
        co2.append(sci_itg.trapz(co2_emissions[i:j], times[i:j]))

    return np.array(co2) / phases_distances


def calculate_cumulative_co2_v1(phases_co2_emissions, phases_distances):
    """
    Calculates cumulative CO2 of cycle phases [CO2g].

    :param phases_co2_emissions:
        CO2 emission of cycle phases [CO2g/km].
    :type phases_co2_emissions: numpy.array

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array

    :return:
        Cumulative CO2 of cycle phases [CO2g].
    :rtype: numpy.array
    """

    return phases_co2_emissions * phases_distances


def calculate_extended_integration_times(
        times, velocities, on_engine, phases_integration_times,
        engine_coolant_temperatures, after_treatment_temperature_threshold,
        stop_velocity):
    """
    Calculates the extended integration times [-].

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param velocities:
        Velocity vector [km/h].
    :type velocities: numpy.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param after_treatment_temperature_threshold:
        Engine coolant temperature threshold when the after treatment system is
        warm [°C].
    :type after_treatment_temperature_threshold: (float, float)

    :param stop_velocity:
        Maximum velocity to consider the vehicle stopped [km/h].
    :type stop_velocity: float

    :return:
        Extended cycle phases integration times [s].
    :rtype: tuple
    """

    lv, pit = velocities <= stop_velocity, phases_integration_times
    pit = set(itertools.chain(*pit))
    hv = np.logical_not(lv)
    j, l, phases = np.argmax(hv), len(lv), []
    while j < l:
        i = np.argmax(lv[j:]) + j
        j = np.argmax(hv[i:]) + i

        if i == j:
            break

        t0, t1 = times[i], times[j]
        if t1 - t0 < 20 or any(t0 <= x <= t1 for x in pit):
            continue

        b = np.logical_not(on_engine[i:j])
        if b.any() and not b.all():
            t = np.median(times[i:j][b])
        else:
            t = (t0 + t1) / 2
        phases.append(t)
    try:
        i = np.searchsorted(engine_coolant_temperatures,
                            (after_treatment_temperature_threshold[1],))[0]
        t = times[i]
        phases.append(t)
    except IndexError:
        pass

    return sorted(phases)


def calculate_extended_cumulative_co2_emissions(
        times, on_engine, extended_integration_times,
        co2_normalization_references, phases_integration_times,
        phases_co2_emissions, phases_distances):
    """
    Calculates the extended cumulative CO2 of cycle phases [CO2g].

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param on_engine:
        If the engine is on [-].
    :type on_engine: numpy.array

    :param extended_integration_times:
        Extended cycle phases integration times [s].
    :type extended_integration_times: tuple

    :param co2_normalization_references:
        CO2 normalization references (e.g., engine loads) [-].
    :type co2_normalization_references: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param phases_co2_emissions:
        CO2 emission of cycle phases [CO2g/km].
    :type phases_co2_emissions: numpy.array

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array

    :return:
        Extended cumulative CO2 of cycle phases [CO2g].
    :rtype: numpy.array
    """

    r = co2_normalization_references.copy()
    r[np.logical_not(on_engine)] = 0
    _cco2, phases = [], []
    cco2 = phases_co2_emissions * phases_distances
    trapz = sci_itg.trapz
    for cco2, (t0, t1) in zip(cco2, phases_integration_times):
        i, j = np.searchsorted(times, (t0, t1))
        if i == j:
            continue
        v = trapz(r[i:j], times[i:j])
        c = [0.0]

        p = [t for t in extended_integration_times if t0 < t < t1]

        for k, t in zip(np.searchsorted(times, p), p):
            phases.append((t0, t))
            t0 = t
            c.append(trapz(r[i:k], times[i:k]) / v)
        phases.append((t0, t1))
        c.append(1.0)

        _cco2.extend(np.diff(c) * cco2)

    return np.array(_cco2), phases


def calculate_phases_co2_emissions(cumulative_co2_emissions, phases_distances):
    """
    Calculates the CO2 emission of cycle phases [CO2g/km].

    :param cumulative_co2_emissions:
        Cumulative CO2 of cycle phases [CO2g].
    :type cumulative_co2_emissions: numpy.array

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array

    :return:
        CO2 emission of cycle phases [CO2g/km].
    :rtype: numpy.array
    """

    return cumulative_co2_emissions / phases_distances


def identify_co2_emissions(
        co2_emissions_model, params_initial_guess, times,
        phases_integration_times, cumulative_co2_emissions):
    """
    Identifies instantaneous CO2 emission vector [CO2g/s].

    :param co2_emissions_model:
        CO2 emissions model (co2_emissions = models(params)).
    :type co2_emissions_model: function

    :param params_initial_guess:
        Initial guess of co2 emission model params.
    :type params_initial_guess: dict

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param cumulative_co2_emissions:
        Cumulative CO2 of cycle phases [CO2g].
    :type cumulative_co2_emissions: numpy.array

    :return:
        The instantaneous CO2 emission vector [CO2g/s].
    :rtype: numpy.array
    """

    co2_emissions = co2_emissions_model(params_initial_guess)[0]
    trapz = sci_itg.trapz
    for cco2, p in zip(cumulative_co2_emissions, phases_integration_times):
        i, j = np.searchsorted(times, p)
        co2_emissions[i:j] *= cco2 / trapz(co2_emissions[i:j], times[i:j])

    return co2_emissions


def define_co2_error_function_on_emissions(co2_emissions_model, co2_emissions):
    """
    Defines an error function (according to co2 emissions time series) to
    calibrate the CO2 emission model params.

    :param co2_emissions_model:
        CO2 emissions model (co2_emissions = models(params)).
    :type co2_emissions_model: function

    :param co2_emissions:
        CO2 instantaneous emissions vector [CO2g/s].
    :type co2_emissions: numpy.array

    :return:
        Error function (according to co2 emissions time series) to calibrate the
        CO2 emission model params.
    :rtype: function
    """

    def error_func(params, sub_values=None):
        x = co2_emissions if sub_values is None else co2_emissions[sub_values]
        y = co2_emissions_model(params, sub_values=sub_values)[0]
        return sk_met.mean_absolute_error(x, y)

    return error_func


def define_co2_error_function_on_phases(
        co2_emissions_model, phases_co2_emissions, times,
        phases_integration_times, phases_distances):
    """
    Defines an error function (according to co2 emissions phases) to
    calibrate the CO2 emission model params.

    :param co2_emissions_model:
        CO2 emissions model (co2_emissions = models(params)).
    :type co2_emissions_model: function

    :param phases_co2_emissions:
        Cumulative CO2 of cycle phases [CO2g].
    :type phases_co2_emissions: numpy.array

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array

    :return:
        Error function (according to co2 emissions phases) to calibrate the CO2
        emission model params.
    :rtype: function
    """

    def error_func(params, phases=None):

        if phases:
            co2 = np.zeros_like(times, dtype=float)
            b = np.zeros_like(times, dtype=bool)
            w = []
            for i, p in enumerate(phases_integration_times):
                if i in phases:
                    m, n = np.searchsorted(times, p)
                    b[m:n] = True
                    w.append(phases_co2_emissions[i])
                else:
                    w.append(0)

            co2[b] = co2_emissions_model(params, sub_values=b)[0]
        else:
            co2 = co2_emissions_model(params)[0]
            w = None  # cumulative_co2_emissions

        cco2 = calculate_cumulative_co2(
            times, phases_integration_times, co2, phases_distances)
        return sk_met.mean_absolute_error(phases_co2_emissions, cco2, w)

    return error_func


def predict_co2_emissions(co2_emissions_model, params):
    """
    Predicts CO2 instantaneous emissions vector [CO2g/s].

    :param co2_emissions_model:
        CO2 emissions model (co2_emissions = models(params)).
    :type co2_emissions_model: function

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: dict

    :return:
        CO2 instantaneous emissions vector [CO2g/s].
    :rtype: numpy.array
    """

    return co2_emissions_model(params)


def calculate_fuel_consumptions(co2_emissions, fuel_carbon_content):
    """
    Calculates the instantaneous fuel consumption vector [g/s].

    :param co2_emissions:
        CO2 instantaneous emissions vector [CO2g/s].
    :type co2_emissions: numpy.array

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :return:
        The instantaneous fuel consumption vector [g/s].
    :rtype: numpy.array
    """

    return co2_emissions / fuel_carbon_content


def calculate_co2_emission(phases_co2_emissions, phases_distances):
    """
    Calculates the CO2 emission of the cycle [CO2g/km].

    :param phases_co2_emissions:
        CO2 emission of cycle phases [CO2g/km].
    :type phases_co2_emissions: numpy.array

    :param phases_distances:
        Cycle phases distances [km].
    :type phases_distances: numpy.array | float

    :return:
        CO2 emission value of the cycle [CO2g/km].
    :rtype: float
    """

    n = sum(phases_co2_emissions * phases_distances)

    if isinstance(phases_distances, float):
        d = phases_distances * len(phases_co2_emissions)
    else:
        d = sum(phases_distances)

    return float(n / d)


def _select_initial_friction_params(co2_params_initial_guess):
    """
    Selects initial guess of friction params l & l2 for the calculation of
    the motoring curve.

    :param co2_params_initial_guess:
        Initial guess of CO2 emission model params.
    :type co2_params_initial_guess: lmfit.Parameters

    :return:
        Initial guess of friction params l & l2.
    :rtype: float, float
    """

    params = co2_params_initial_guess.valuesdict()

    return dsp_utl.selector(('l', 'l2'), params, output_type='list')


def define_initial_co2_emission_model_params_guess(
        params, engine_type, engine_normalization_temperature,
        engine_thermostat_temperature_window, is_cycle_hot=False,
        bounds=None):
    """
    Selects initial guess and bounds of co2 emission model params.

    :param params:
        CO2 emission model params (a2, b2, a, b, c, l, l2, t, trg).
    :type params: dict

    :param engine_type:
        Engine type (positive turbo, positive natural aspiration, compression).
    :type engine_type: str

    :param engine_normalization_temperature:
        Engine normalization temperature [°C].
    :type engine_normalization_temperature: float

    :param engine_thermostat_temperature_window:
        Thermostat engine temperature limits [°C].
    :type engine_thermostat_temperature_window: (float, float)

    :param is_cycle_hot:
        Is an hot cycle?
    :type is_cycle_hot: bool, optional

    :param bounds:
        Parameters bounds.
    :type bounds: bool, optional

    :return:
        Initial guess of co2 emission model params and of friction params.
    :rtype: lmfit.Parameters, list[float]
    """

    bounds = bounds or {}
    par = defaults.dfl.functions.define_initial_co2_emission_model_params_guess
    default = copy.deepcopy(par.CO2_PARAMS)[engine_type]
    default['trg'] = {
        'value': engine_normalization_temperature,
        'min': engine_thermostat_temperature_window[0],
        'max': engine_thermostat_temperature_window[1],
        'vary': False
    }

    if is_cycle_hot:
        default['t0'].update({'value': 0.0, 'vary': False})
        default['t1'].update({'value': 0.0, 'vary': False})

    p = lmfit.Parameters()
    from ..defaults import EPS

    for k, kw in sorted(default.items()):
        kw['name'] = k
        kw['value'] = params.get(k, kw['value'])

        if k in bounds:
            b = bounds[k]
            kw['min'] = b.get('min', kw.get('min', None))
            kw['max'] = b.get('max', kw.get('max', None))
            kw['vary'] = b.get('vary', kw.get('vary', True))
        elif 'vary' not in kw:
            kw['vary'] = k not in params

        if 'min' in kw and kw['value'] < kw['min']:
            kw['min'] = kw['value'] - EPS
        if 'max' in kw and kw['value'] > kw['max']:
            kw['max'] = kw['value'] + EPS

        if 'min' in kw and 'max' in kw and kw['min'] == kw['max']:
            kw['vary'] = False
            kw['max'] = kw['min'] = None
        kw['max'] = kw['min'] = None
        p.add(**kw)

    friction_params = _select_initial_friction_params(p)
    if not missing_co2_params(params):
        p = dsp_utl.NONE

    return p, friction_params


def calculate_after_treatment_temperature_threshold(
        engine_normalization_temperature, initial_engine_temperature):
    """
    Calculates the engine coolant temperature when the after treatment system
    is warm [°C].

    :param engine_normalization_temperature:
        Engine normalization temperature [°C].
    :type engine_normalization_temperature: float

    :param initial_engine_temperature:
        Initial engine temperature [°C].
    :type initial_engine_temperature: float

    :return:
        Engine coolant temperature threshold when the after treatment system is
        warm [°C].
    :rtype: (float, float)
    """

    ti = 273 + initial_engine_temperature
    t = (273 + engine_normalization_temperature) / ti - 1
    T_mean = 40 * t + initial_engine_temperature
    T_end = 40 * t ** 2 + T_mean

    return T_mean, T_end


def define_tau_function(after_treatment_temperature_threshold):
    """
    Defines tau-function of the extended Willans curve.

    :param after_treatment_temperature_threshold:
        Engine coolant temperature threshold when the after treatment system is
        warm [°C].
    :type after_treatment_temperature_threshold: (float, float)

    :return:
        Tau-function of the extended Willans curve.
    :rtype: function
    """
    T_mean, T_end = np.array(after_treatment_temperature_threshold) + 273
    s = np.log(T_end / T_mean) / sci_sta.norm.ppf(0.95)
    f = sci_sta.lognorm(s, 0, T_mean).cdf

    def tau_function(t0, t1, temp):
        return t0 - (t1 - t0) * f(temp + 273)

    return tau_function


def _set_attr(params, data, default=False, attr='vary'):
    """
    Set attribute to CO2 emission model parameters.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).
    :type params: lmfit.Parameters

    :param data:
        Parameter ids to be set or key/value to set.
    :type data: list | dict

    :param default:
        Default value to set if a list of parameters ids is provided.
    :type default: bool | float

    :param attr:
        Parameter attribute to set.
    :type attr: str

    :return:
        CO2 emission model parameters.
    :rtype: lmfit.Parameters
    """
    if not isinstance(data, dict):
        data = dict.fromkeys(data, default)

    for k, v in data.items():
        params[k].set(**{attr: v})

    return params


def calibrate_co2_params(
        engine_coolant_temperatures, co2_error_function_on_emissions,
        co2_error_function_on_phases, co2_params_initial_guess, is_cycle_hot):
    """
    Calibrates the CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg
    ).

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param co2_error_function_on_emissions:
        Error function (according to co2 emissions time series) to calibrate the
        CO2 emission model params.
    :type co2_error_function_on_emissions: function

    :param co2_error_function_on_phases:
        Error function (according to co2 emissions phases) to calibrate the CO2
        emission model params.
    :type co2_error_function_on_phases: function

    :param co2_params_initial_guess:
        Initial guess of CO2 emission model params.
    :type co2_params_initial_guess: Parameters

    :param is_cycle_hot:
        Is an hot cycle?
    :type is_cycle_hot: bool

    :return:
        Calibrated CO2 emission model parameters (a2, b2, a, b, c, l, l2, t,
        trg) and their calibration statuses.
    :rtype: (lmfit.Parameters, list)
    """

    p = copy.deepcopy(co2_params_initial_guess)
    vary = {k: v.vary for k, v in p.items()}
    values = {k: v._val for k, v in p.items()}

    cold = np.zeros_like(engine_coolant_temperatures, dtype=bool)
    if not is_cycle_hot:
        i = co2_utl.argmax(engine_coolant_temperatures >= p['trg'].value)
        cold[:i] = True
    hot = np.logical_not(cold)

    success = [(True, copy.deepcopy(p))]

    def calibrate(id_p, p, **kws):
        _set_attr(p, id_p, default=False)
        p, s = calibrate_model_params(co2_error_function_on_emissions, p, **kws)
        _set_attr(p, vary)
        success.append((s, copy.deepcopy(p)))
        return p

    cold_p = ['t0', 't1']
    if hot.any():
        _set_attr(p, ['t0', 't1'], default=0.0, attr='value')
        p = calibrate(cold_p, p, sub_values=hot)
    else:
        success.append((True, copy.deepcopy(p)))

    if cold.any():
        _set_attr(p, {'t0': values['t0'], 't1': values['t1']}, attr='value')
        hot_p = ['a2', 'a', 'b', 'c', 'l', 'l2']
        p = calibrate(hot_p, p, sub_values=cold)
    else:
        success.append((True, copy.deepcopy(p)))
        _set_attr(p, ['t0', 't1'], default=0.0, attr='value')
        _set_attr(p, cold_p, default=False)

    p = restrict_bounds(p)

    p, s = calibrate_model_params(co2_error_function_on_emissions, p)
    success.append((s, copy.deepcopy(p)))
    _set_attr(p, vary)

    return p, success


def restrict_bounds(co2_params):
    """
    Returns restricted bounds of CO2 emission model params (a2, b2, a, b, c, l,
    l2, t, trg).

    :param co2_params:
        CO2 emission model params (a2, b2, a, b, c, l, l2, t, trg).
    :type co2_params: Parameters

    :return:
        Restricted bounds of CO2 emission model params (a2, b2, a, b, c, l, l2,
        t, trg).
    :rtype: dict
    """
    p = copy.deepcopy(co2_params)
    m = defaults.dfl.functions.restrict_bounds.CO2_PARAMS_LIMIT_MULTIPLIERS

    def _limits(k, v):
        try:
            v = tuple(np.asarray(m[k]) * v.value)
            return min(v), max(v)
        except KeyError:
            return v.min, v.max

    for k, v in p.items():
        v.min, v.max = _limits(k, v)

        if v.max == v.min:
            v.set(value=v.min, min=None, max=None, vary=False)

    return p


def calibrate_model_params(error_function, params, *args, **kws):
    """
    Calibrates the model params minimising the error_function.

    :param error_function:
        Model error function.
    :type error_function: function

    :param params:
        Initial guess of model params.

        If not specified a brute force is used to identify the best initial
        guess with in the bounds.
    :type params: dict, optional

    :return:
        Calibrated model params.
    :rtype: dict
    """

    if not any(p.vary for p in params.values()):
        return params, True

    if callable(error_function):
        error_f = error_function
    else:
        def error_f(p, *a, **k):
            return sum(f(p, *a, **k) for f in error_function)

    min_e_and_p = [np.inf, copy.deepcopy(params)]

    def error_func(params, *args, **kwargs):
        res = error_f(params, *args, **kwargs)

        if res < min_e_and_p[0]:
            min_e_and_p[0], min_e_and_p[1] = (res, copy.deepcopy(params))

        return res

    # See #7: Neither BFGS nor SLSQP fix "solution families".
    # leastsq: Improper input: N=6 must not exceed M=1.
    # nelder is stable (297 runs, 14 vehicles) [average time 181s/14 vehicles].
    # lbfgsb is unstable (2 runs, 4 vehicles) [average time 23s/4 vehicles].
    # cg is stable (20 runs, 4 vehicles) [average time 37s/4 vehicles].
    # newton: Jacobian is required for Newton-CG method
    # cobyla is unstable (8 runs, 4 vehicles) [average time 16s/4 vehicles].
    # tnc is unstable (6 runs, 4 vehicles) [average time 23s/4 vehicles].
    # dogleg: Jacobian is required for dogleg minimization.
    # slsqp is unstable (4 runs, 4 vehicles) [average time 18s/4 vehicles].
    # differential_evolution is unstable (1 runs, 4 vehicles)
    # [average time 270s/4 vehicles].
    res = _minimize(error_func, params, args=args, kws=kws, method='nelder')

    # noinspection PyUnresolvedReferences
    return (res.params if res.success else min_e_and_p[1]), res.success


# correction of lmfit bug.
def _minimize(fcn, params, method='leastsq', args=None, kws=None,
              scale_covar=True, iter_cb=None, **fit_kws):
    fitter = _Minimizer(fcn, params, fcn_args=args, fcn_kws=kws,
                        iter_cb=iter_cb, scale_covar=scale_covar, **fit_kws)

    return fitter.minimize(method=method)


class _Minimizer(lmfit.Minimizer):
    def scalar_minimize(self, method='Nelder-Mead', params=None, **kws):
        """
        Use one of the scalar minimization methods from
        scipy.optimize.minimize.

        Parameters
        ----------
        method : str, optional
            Name of the fitting method to use.
            One of:
                'Nelder-Mead' (default)
                'L-BFGS-B'
                'Powell'
                'CG'
                'Newton-CG'
                'COBYLA'
                'TNC'
                'trust-ncg'
                'dogleg'
                'SLSQP'
                'differential_evolution'

        params : Parameters, optional
           Parameters to use as starting points.
        kws : dict, optional
            Minimizer options pass to scipy.optimize.minimize.

        If the objective function returns a numpy array instead
        of the expected scalar, the sum of squares of the array
        will be used.

        Note that bounds and constraints can be set on Parameters
        for any of these methods, so are not supported separately
        for those designed to use bounds. However, if you use the
        differential_evolution option you must specify finite
        (min, max) for each Parameter.

        Returns
        -------
        success : bool
            Whether the fit was successful.

        """
        from lmfit.minimizer import HAS_SCALAR_MIN
        if not HAS_SCALAR_MIN:
            raise NotImplementedError

        result = self.prepare_fit(params=params)
        vars = result.init_vals
        params = result.params

        fmin_kws = dict(method=method,
                        options={'maxiter': 1000 * (len(vars) + 1)})
        fmin_kws.update(self.kws)
        fmin_kws.update(kws)

        # hess supported only in some methods
        if 'hess' in fmin_kws and method not in ('Newton-CG',
                                                 'dogleg', 'trust-ncg'):
            fmin_kws.pop('hess')

        # jac supported only in some methods (and Dfun could be used...)
        if 'jac' not in fmin_kws and fmin_kws.get('Dfun', None) is not None:
            self.jacfcn = fmin_kws.pop('jac')
            fmin_kws['jac'] = self.__jacobian

        if 'jac' in fmin_kws and method not in ('CG', 'BFGS', 'Newton-CG',
                                                'dogleg', 'trust-ncg'):
            self.jacfcn = None
            fmin_kws.pop('jac')

        if method == 'differential_evolution':
            from lmfit.minimizer import _differential_evolution
            fmin_kws['method'] = _differential_evolution
            bounds = [(par.min, par.max) for par in params.values()]
            if not np.all(np.isfinite(bounds)):
                raise ValueError('With differential evolution finite bounds '
                                 'are required for each parameter')
            bounds = [(-np.pi / 2., np.pi / 2.)] * len(vars)
            fmin_kws['bounds'] = bounds

            # in scipy 0.14 this can be called directly from scipy_minimize
            # When minimum scipy is 0.14 the following line and the else
            # can be removed.
            ret = _differential_evolution(self.penalty, vars, **fmin_kws)
        else:
            from lmfit.minimizer import scipy_minimize
            ret = scipy_minimize(self.penalty, vars, **fmin_kws)

        result.aborted = self._abort
        self._abort = False

        for attr, val in ret.items():
            if not attr.startswith('_'):
                setattr(result, attr, val)

        result.chisqr = result.residual = self.__residual(ret.x)
        result.nvarys = len(vars)
        result.ndata = 1
        result.nfree = 1
        if isinstance(result.residual, np.ndarray):
            # noinspection PyUnresolvedReferences
            result.chisqr = (result.chisqr ** 2).sum()
            result.ndata = len(result.residual)
            result.nfree = result.ndata - result.nvarys
        result.redchi = result.chisqr / result.nfree
        _log_likelihood = result.ndata * np.log(result.redchi)
        result.aic = _log_likelihood + 2 * result.nvarys
        result.bic = _log_likelihood + np.log(result.ndata) * result.nvarys

        return result


def calculate_phases_willans_factors(
        params, engine_fuel_lower_heating_value, engine_stroke, engine_capacity,
        min_engine_on_speed, fmep_model, times, phases_integration_times,
        engine_speeds_out, engine_powers_out, velocities, accelerations,
        motive_powers, engine_coolant_temperatures, missing_powers,
        angle_slopes):
    """
    Calculates the Willans factors for each phase.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: lmfit.Parameters

    :param engine_fuel_lower_heating_value:
        Fuel lower heating value [kJ/kg].
    :type engine_fuel_lower_heating_value: float

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param min_engine_on_speed:
        Minimum engine speed to consider the engine to be on [RPM].
    :type min_engine_on_speed: float

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param phases_integration_times:
        Cycle phases integration times [s].
    :type phases_integration_times: tuple

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: numpy.array

    :param engine_powers_out:
        Engine power vector [kW].
    :type engine_powers_out: numpy.array

    :param velocities:
        Velocity vector [km/h].
    :type velocities: numpy.array

    :param accelerations:
        Acceleration vector [m/s2].
    :type accelerations: numpy.array

    :param motive_powers:
        Motive power [kW].
    :type motive_powers: numpy.array

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param missing_powers:
        Missing engine power [kW].
    :type missing_powers: numpy.array

    :param angle_slopes:
        Angle slope vector [rad].
    :type angle_slopes: numpy.array

    :return:
        Willans factors:

        - av_velocities                         [km/h]
        - av_slope                              [rad]
        - distance                              [km]
        - init_temp                             [°C]
        - av_temp                               [°C]
        - end_temp                              [°C]
        - av_vel_pos_mov_pow                    [kw/h]
        - av_pos_motive_powers                  [kW]
        - sec_pos_mov_pow                       [s]
        - av_neg_motive_powers                  [kW]
        - sec_neg_mov_pow                       [s]
        - av_pos_accelerations                  [m/s2]
        - av_engine_speeds_out_pos_pow          [RPM]
        - av_pos_engine_powers_out              [kW]
        - engine_bmep_pos_pow                   [bar]
        - mean_piston_speed_pos_pow             [m/s]
        - fuel_mep_pos_pow                      [bar]
        - fuel_consumption_pos_pow              [g/sec]
        - willans_a                             [g/kWh]
        - willans_b                             [g/h]
        - specific_fuel_consumption             [g/kWh]
        - indicated_efficiency                  [-]
        - willans_efficiency                    [-]

    :rtype: dict
    """

    factors = []

    for p in phases_integration_times:
        i, j = np.searchsorted(times, p)

        factors.append(calculate_willans_factors(
            params, engine_fuel_lower_heating_value, engine_stroke,
            engine_capacity, min_engine_on_speed, fmep_model,
            engine_speeds_out[i:j], engine_powers_out[i:j], times[i:j],
            velocities[i:j], accelerations[i:j], motive_powers[i:j],
            engine_coolant_temperatures[i:j], missing_powers[i:j],
            angle_slopes[i:j]
        ))

    return factors


def calculate_willans_factors(
        params, engine_fuel_lower_heating_value, engine_stroke, engine_capacity,
        min_engine_on_speed, fmep_model, engine_speeds_out, engine_powers_out,
        times, velocities, accelerations, motive_powers,
        engine_coolant_temperatures, missing_powers, angle_slopes):
    """
    Calculates the Willans factors.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: lmfit.Parameters

    :param engine_fuel_lower_heating_value:
        Fuel lower heating value [kJ/kg].
    :type engine_fuel_lower_heating_value: float

    :param engine_stroke:
        Engine stroke [mm].
    :type engine_stroke: float

    :param engine_capacity:
        Engine capacity [cm3].
    :type engine_capacity: float

    :param min_engine_on_speed:
        Minimum engine speed to consider the engine to be on [RPM].
    :type min_engine_on_speed: float

    :param engine_speeds_out:
        Engine speed vector [RPM].
    :type engine_speeds_out: numpy.array

    :param engine_powers_out:
        Engine power vector [kW].
    :type engine_powers_out: numpy.array

    :param times:
        Time vector [s].
    :type times: numpy.array

    :param velocities:
        Velocity vector [km/h].
    :type velocities: numpy.array

    :param accelerations:
        Acceleration vector [m/s2].
    :type accelerations: numpy.array

    :param motive_powers:
        Motive power [kW].
    :type motive_powers: numpy.array

    :param engine_coolant_temperatures:
        Engine coolant temperature vector [°C].
    :type engine_coolant_temperatures: numpy.array

    :param missing_powers:
        Missing engine power [kW].
    :type missing_powers: numpy.array

    :param angle_slopes:
        Angle slope vector [rad].
    :type angle_slopes: numpy.array

    :return:
        Willans factors:

        - av_velocities                         [km/h]
        - av_slope                              [rad]
        - distance                              [km]
        - init_temp                             [°C]
        - av_temp                               [°C]
        - end_temp                              [°C]
        - av_vel_pos_mov_pow                    [kw/h]
        - av_pos_motive_powers                  [kW]
        - sec_pos_mov_pow                       [s]
        - av_neg_motive_powers                  [kW]
        - sec_neg_mov_pow                       [s]
        - av_pos_accelerations                  [m/s2]
        - av_engine_speeds_out_pos_pow          [RPM]
        - av_pos_engine_powers_out              [kW]
        - engine_bmep_pos_pow                   [bar]
        - mean_piston_speed_pos_pow             [m/s]
        - fuel_mep_pos_pow                      [bar]
        - fuel_consumption_pos_pow              [g/sec]
        - willans_a                             [g/kWh]
        - willans_b                             [g/h]
        - specific_fuel_consumption             [g/kWh]
        - indicated_efficiency                  [-]
        - willans_efficiency                    [-]

    :rtype: dict
    """

    from . import calculate_mean_piston_speeds
    av = np.average

    w = np.zeros_like(times, dtype=float)
    t = (times[:-1] + times[1:]) / 2
    # noinspection PyUnresolvedReferences
    w[0], w[1:-1], w[-1] = t[0] - times[0], np.diff(t), times[-1] - t[-1]

    f = {
        'av_velocities': av(velocities, weights=w),  # [km/h]
        'av_slope': av(angle_slopes, weights=w),
        'has_sufficient_power': not missing_powers.any(),
        'max_power_required': max(engine_powers_out + missing_powers)
    }

    f['distance'] = f['av_velocities'] * (times[-1] - times[0]) / 3600.0  # [km]

    b = engine_powers_out >= 0
    if b.any():
        p = params.valuesdict()
        _w = w[b]
        av_s = av(engine_speeds_out[b], weights=_w)
        av_p = av(engine_powers_out[b], weights=_w)
        av_mp = av(missing_powers[b], weights=_w)

        n_p = calculate_brake_mean_effective_pressures(
            av_s, av_p, engine_capacity, min_engine_on_speed
        )
        n_s = calculate_mean_piston_speeds(av_s, engine_stroke)

        f_mep, wfa = fmep_model(p, n_s, n_p, 1, 0)[:2]

        c = engine_capacity / engine_fuel_lower_heating_value * av_s
        fc = f_mep * c / 1200.0
        ieff = av_p / (fc * engine_fuel_lower_heating_value) * 1000.0

        willans_a = 3600000.0 / engine_fuel_lower_heating_value / wfa
        willans_b = fmep_model(p, n_s, 0, 1, 0)[0] * c * 3.0

        sfc = willans_a + willans_b / av_p

        willans_eff = 3600000.0 / (sfc * engine_fuel_lower_heating_value)

        f.update({
            'av_engine_speeds_out_pos_pow': av_s,  # [RPM]
            'av_pos_engine_powers_out': av_p,  # [kW]
            'av_missing_powers_pos_pow': av_mp,  # [kW]
            'engine_bmep_pos_pow': n_p,  # [bar]
            'mean_piston_speed_pos_pow': n_s,  # [m/s]
            'fuel_mep_pos_pow': f_mep,  # [bar]
            'fuel_consumption_pos_pow': fc,  # [g/sec]
            'willans_a': willans_a,  # [g/kW]
            'willans_b': willans_b,  # [g]
            'specific_fuel_consumption': sfc,  # [g/kWh]
            'indicated_efficiency': ieff,  # [-]
            'willans_efficiency': willans_eff  # [-]
        })

    b = motive_powers > 0
    if b.any():
        _w = w[b]
        f['av_vel_pos_mov_pow'] = av(velocities[b], weights=_w)  # [km/h]
        f['av_pos_motive_powers'] = av(motive_powers[b], weights=_w)  # [kW]
        f['sec_pos_mov_pow'] = np.sum(_w)  # [s]

    b = accelerations > 0
    if b.any():
        _w = w[b]
        f['av_pos_accelerations'] = av(accelerations[b], weights=_w)  # [m/s2]

    b = motive_powers < 0
    if b.any():
        _w = w[b]
        f['av_neg_motive_powers'] = av(motive_powers[b], weights=_w)  # [kW]
        f['sec_neg_mov_pow'] = np.sum(_w)  # [s]

    f['init_temp'] = engine_coolant_temperatures[0]  # [°C]
    f['av_temp'] = av(engine_coolant_temperatures, weights=w)  # [°C]
    f['end_temp'] = engine_coolant_temperatures[-1]  # [°C]

    return f


def calculate_optimal_efficiency(params, mean_piston_speeds):
    """
    Calculates the optimal efficiency [-] and t.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).

        The missing parameters are set equal to zero.
    :type params: lmfit.Parameters

    :param mean_piston_speeds:
        Mean piston speed vector [m/s].
    :type mean_piston_speeds: numpy.array

    :return:
        Optimal efficiency and the respective parameters:

        - mean_piston_speeds [m/s],
        - engine_bmep [bar],
        - efficiency [-].

    :rtype: dict[str | tuple]
    """

    n_s = np.linspace(mean_piston_speeds.min(), mean_piston_speeds.max(), 10)
    bmep, eff = _calculate_optimal_point(params, n_s)

    return {'mean_piston_speeds': n_s, 'engine_bmep': bmep, 'efficiency': eff}


def _calculate_optimal_point(params, n_speed):
    A, B, C = _fuel_ABC(n_speed, 0, 1, **params)
    ac4, B2 = 4 * A * C, B ** 2
    sabc = np.sqrt(ac4 * B2)
    n = sabc - ac4

    y = 2 * C - sabc / (2 * A)
    eff = n / (B - np.sqrt(B2 - sabc - n))

    return y, eff


# noinspection PyUnusedLocal
def missing_co2_params(params, *args, _not=False):
    """
    Checks if all co2_params are not defined.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).
    :type params: dict | lmfit.Parameters

    :param _not:
        If True the function checks if not all co2_params are defined.
    :type _not: bool

    :return:
        If is missing some parameter.
    :rtype: bool
    """

    s = {'a', 'b', 'c', 'a2', 'b2', 'l', 'l2', 't0', 't1', 'trg'}

    if _not:
        return set(params).issuperset(s)

    return not set(params).issuperset(s)


def define_co2_params_calibrated(params):
    """
    Defines the calibrated co2_params if all co2_params are given.

    :param params:
        CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg).
    :type params: dict | lmfit.Parameters

    :return:
        Calibrated CO2 emission model parameters (a2, b2, a, b, c, l, l2, t,
        trg) and their calibration statuses.
    :rtype: (lmfit.Parameters, list)
    """

    if isinstance(params, lmfit.Parameters):
        p = params
    else:
        p = lmfit.Parameters()
        for k, v in params.items():
            p.add(k, value=v, vary=False)

    success = [(None, copy.deepcopy(p))] * 4

    return p, success


def calibrate_co2_params_v1(
        co2_emissions_model, fuel_consumptions, fuel_carbon_content,
        co2_params_initial_guess):
    """
    Calibrates the CO2 emission model parameters (a2, b2, a, b, c, l, l2, t, trg
    ).

    :param co2_emissions_model:
        CO2 emissions model (co2_emissions = models(params)).
    :type co2_emissions_model: function

    :param fuel_consumptions:
        Instantaneous fuel consumption vector [g/s].
    :type fuel_consumptions: numpy.array

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :param co2_params_initial_guess:
        Initial guess of CO2 emission model params.
    :type co2_params_initial_guess: Parameters:param co2_params_initial_guess:

    :return:
        Calibrated CO2 emission model parameters (a2, b2, a, b, c, l, l2, t,
        trg) and their calibration statuses.
    :rtype: (lmfit.Parameters, list)
    """

    co2 = fuel_consumptions * fuel_carbon_content
    err = define_co2_error_function_on_emissions(co2_emissions_model, co2)
    p = copy.deepcopy(co2_params_initial_guess)
    success = [(True, copy.deepcopy(p))]

    p, s = calibrate_model_params(err, p)
    success += [(s, p), (None, None), (None, None)]

    return p, success


def calculate_phases_fuel_consumptions(
        phases_co2_emissions, fuel_carbon_content, fuel_density):
    """
    Calculates cycle phases fuel consumption [l/100km].

    :param phases_co2_emissions:
        CO2 emission of cycle phases [CO2g/km].
    :type phases_co2_emissions: numpy.array

    :param fuel_carbon_content:
        Fuel carbon content [CO2g/g].
    :type fuel_carbon_content: float

    :param fuel_density:
        Fuel density [g/l].
    :type fuel_density: float

    :return:
        Fuel consumption of cycle phases [l/100km].
    :rtype: tuple
    """

    c = 100.0 / (fuel_density * fuel_carbon_content)

    return tuple(np.asarray(phases_co2_emissions) * c)


def co2_emission():
    """
    Defines the engine CO2 emission sub model.

    .. dispatcher:: d

        >>> d = co2_emission()

    :return:
        The engine CO2 emission sub model.
    :rtype: co2mpas.dispatcher.Dispatcher
    """

    d = dsp.Dispatcher(
        name='Engine CO2 emission sub model',
        description='Calculates CO2 emission.'
    )

    d.add_data(
        data_id='active_cylinder_ratios',
        default_value=defaults.dfl.values.active_cylinder_ratios
    )

    d.add_data(
        data_id='engine_has_cylinder_deactivation',
        default_value=defaults.dfl.values.engine_has_cylinder_deactivation
    )

    d.add_function(
        function=define_fmep_model,
        inputs=['full_bmep_curve', 'active_cylinder_ratios',
                'engine_has_cylinder_deactivation'],
        outputs=['fmep_model']
    )

    d.add_data(
        data_id='fuel_type'
    )
    # d.add_function(
    #    function=default_engine_fuel_lower_heating_value,
    #    inputs=['fuel_type'],
    #    outputs=['engine_fuel_lower_heating_value'],
    # )

    # d.add_function(
    #    function=default_fuel_carbon_content,
    #    inputs=['fuel_type'],
    #    outputs=['fuel_carbon_content'],
    #    weight=3
    # )

    d.add_function(
        function=calculate_fuel_carbon_content_percentage,
        inputs=['fuel_carbon_content'],
        outputs=['fuel_carbon_content_percentage']
    )

    d.add_function(
        function=calculate_fuel_carbon_content,
        inputs=['fuel_carbon_content_percentage'],
        outputs=['fuel_carbon_content']
    )

    d.add_function(
        function=calculate_brake_mean_effective_pressures,
        inputs=['engine_speeds_out', 'engine_powers_out', 'engine_capacity',
                'min_engine_on_speed'],
        outputs=['brake_mean_effective_pressures']
    )

    d.add_function(
        function=calculate_after_treatment_temperature_threshold,
        inputs=['engine_thermostat_temperature',
                'initial_engine_temperature'],
        outputs=['after_treatment_temperature_threshold']
    )

    d.add_function(
        function=define_tau_function,
        inputs=['after_treatment_temperature_threshold'],
        outputs=['tau_function']
    )

    d.add_data(
        data_id='stop_velocity',
        default_value=defaults.dfl.values.stop_velocity
    )

    d.add_data(
        data_id='min_engine_on_speed',
        default_value=defaults.dfl.values.min_engine_on_speed
    )

    d.add_function(
        function=calculate_extended_integration_times,
        inputs=['times', 'velocities', 'on_engine', 'phases_integration_times',
                'engine_coolant_temperatures',
                'after_treatment_temperature_threshold', 'stop_velocity'],
        outputs=['extended_integration_times'],
    )

    d.add_function(
        function=calculate_extended_cumulative_co2_emissions,
        inputs=['times', 'on_engine', 'extended_integration_times',
                'co2_normalization_references', 'phases_integration_times',
                'phases_co2_emissions', 'phases_distances'],
        outputs=['extended_cumulative_co2_emissions',
                 'extended_phases_integration_times']
    )

    d.add_data(
        data_id='idle_fuel_consumption_initial_guess',
        default_value=None,
        description='Initial guess of fuel consumption '
                    'at hot idle engine speed [g/s].'
    )

    d.add_function(
        function=define_idle_fuel_consumption_model,
        inputs=['idle_engine_speed', 'engine_capacity', 'engine_stroke',
                'engine_fuel_lower_heating_value', 'fmep_model',
                'idle_fuel_consumption_initial_guess'],
        outputs=['idle_fuel_consumption_model']
    )

    d.add_function(
        function=calculate_engine_idle_fuel_consumption,
        inputs=['idle_fuel_consumption_model', 'co2_params_calibrated'],
        outputs=['engine_idle_fuel_consumption']
    )

    d.add_function(
        function=define_co2_emissions_model,
        inputs=['engine_speeds_out', 'engine_powers_out',
                'mean_piston_speeds', 'brake_mean_effective_pressures',
                'engine_coolant_temperatures', 'on_engine',
                'engine_fuel_lower_heating_value', 'idle_engine_speed',
                'engine_stroke', 'engine_capacity',
                'idle_fuel_consumption_model', 'fuel_carbon_content',
                'min_engine_on_speed', 'tau_function', 'fmep_model'],
        outputs=['co2_emissions_model']
    )

    d.add_data(
        data_id='is_cycle_hot',
        default_value=defaults.dfl.values.is_cycle_hot
    )

    d.add_function(
        function=define_initial_co2_emission_model_params_guess,
        inputs=['co2_params', 'engine_type', 'engine_thermostat_temperature',
                'engine_thermostat_temperature_window', 'is_cycle_hot'],
        outputs=['co2_params_initial_guess', 'initial_friction_params'],
    )

    d.add_function(
        function=select_phases_integration_times,
        inputs=['cycle_type'],
        outputs=['phases_integration_times']
    )

    d.add_function(
        function=calculate_phases_distances,
        inputs=['times', 'phases_integration_times', 'velocities'],
        outputs=['phases_distances']
    )

    d.add_function(
        function=calculate_phases_distances,
        inputs=['times', 'extended_phases_integration_times', 'velocities'],
        outputs=['extended_phases_distances']
    )

    d.add_function(
        function=calculate_phases_co2_emissions,
        inputs=['extended_cumulative_co2_emissions',
                'extended_phases_distances'],
        outputs=['extended_phases_co2_emissions']
    )

    d.add_function(
        function=dsp_utl.bypass,
        inputs=['phases_integration_times', 'cumulative_co2_emissions',
                'phases_distances'],
        outputs=['extended_phases_integration_times',
                 'extended_cumulative_co2_emissions',
                 'extended_phases_distances'],
        weight=300
    )

    d.add_function(
        function=calculate_cumulative_co2_v1,
        inputs=['phases_co2_emissions', 'phases_distances'],
        outputs=['cumulative_co2_emissions']
    )

    d.add_function(
        function=identify_co2_emissions,
        inputs=['co2_emissions_model', 'co2_params_initial_guess', 'times',
                'extended_phases_integration_times',
                'extended_cumulative_co2_emissions'],
        outputs=['identified_co2_emissions'],
        weight=5
    )

    d.add_function(
        function=dsp_utl.bypass,
        inputs=['co2_emissions'],
        outputs=['identified_co2_emissions']
    )

    d.add_function(
        function=define_co2_error_function_on_emissions,
        inputs=['co2_emissions_model', 'identified_co2_emissions'],
        outputs=['co2_error_function_on_emissions']
    )

    d.add_function(
        function=define_co2_error_function_on_phases,
        inputs=['co2_emissions_model', 'phases_co2_emissions', 'times',
                'phases_integration_times', 'phases_distances'],
        outputs=['co2_error_function_on_phases']
    )

    d.add_function(
        function=calibrate_co2_params,
        inputs=['engine_coolant_temperatures',
                'co2_error_function_on_emissions',
                'co2_error_function_on_phases', 'co2_params_initial_guess',
                'is_cycle_hot'],
        outputs=['co2_params_calibrated', 'calibration_status']
    )

    d.add_function(
        function=define_co2_params_calibrated,
        inputs=['co2_params'],
        outputs=['co2_params_calibrated', 'calibration_status'],
        input_domain=functools.partial(missing_co2_params, _not=True)
    )

    d.add_function(
        function=predict_co2_emissions,
        inputs=['co2_emissions_model', 'co2_params_calibrated'],
        outputs=['co2_emissions', 'active_cylinders']
    )

    d.add_data(
        data_id='co2_params',
        default_value=defaults.dfl.values.co2_params.copy()
    )

    d.add_function(
        function_id='calculate_phases_co2_emissions',
        function=calculate_cumulative_co2,
        inputs=['times', 'phases_integration_times', 'co2_emissions',
                'phases_distances'],
        outputs=['phases_co2_emissions']
    )

    d.add_function(
        function=calculate_fuel_consumptions,
        inputs=['co2_emissions', 'fuel_carbon_content'],
        outputs=['fuel_consumptions']
    )

    d.add_function(
        function=calculate_co2_emission,
        inputs=['phases_co2_emissions', 'phases_distances'],
        outputs=['co2_emission_value']
    )

    d.add_data(
        data_id='co2_emission_low',
        description='CO2 emission on low WLTP phase [CO2g/km].'
    )

    d.add_data(
        data_id='co2_emission_medium',
        description='CO2 emission on medium WLTP phase [CO2g/km].'
    )

    d.add_data(
        data_id='co2_emission_high',
        description='CO2 emission on high WLTP phase [CO2g/km].'
    )

    d.add_data(
        data_id='co2_emission_extra_high',
        description='CO2 emission on extra high WLTP phase [CO2g/km].'
    )

    d.add_function(
        function_id='merge_wltp_phases_co2_emission',
        function=dsp_utl.bypass,
        inputs=['co2_emission_low', 'co2_emission_medium', 'co2_emission_high',
                'co2_emission_extra_high'],
        outputs=['phases_co2_emissions']
    )

    d.add_data(
        data_id='co2_emission_UDC',
        description='CO2 emission on UDC NEDC phase [CO2g/km].'
    )

    d.add_data(
        data_id='co2_emission_EUDC',
        description='CO2 emission on EUDC NEDC phase [CO2g/km].'
    )

    d.add_function(
        function_id='merge_nedc_phases_co2_emission',
        function=dsp_utl.bypass,
        inputs=['co2_emission_UDC', 'co2_emission_EUDC'],
        outputs=['phases_co2_emissions']
    )

    d.add_data(
        data_id='enable_willans',
        default_value=defaults.dfl.values.enable_willans,
        description='Enable the calculation of Willans coefficients for '
                    'the cycle?'
    )

    d.add_function(
        function=dsp_utl.add_args(calculate_willans_factors),
        inputs=['enable_willans', 'co2_params_calibrated',
                'engine_fuel_lower_heating_value', 'engine_stroke',
                'engine_capacity', 'min_engine_on_speed', 'fmep_model',
                'engine_speeds_out', 'engine_powers_out', 'times', 'velocities',
                'accelerations', 'motive_powers', 'engine_coolant_temperatures',
                'missing_powers', 'angle_slopes'],
        outputs=['willans_factors'],
        input_domain=lambda *args: args[0]
    )

    d.add_data(
        data_id='enable_phases_willans',
        default_value=defaults.dfl.values.enable_phases_willans,
        description='Enable the calculation of Willans coefficients for '
                    'all phases?'
    )

    d.add_function(
        function=dsp_utl.add_args(calculate_phases_willans_factors),
        inputs=['enable_phases_willans', 'co2_params_calibrated',
                'engine_fuel_lower_heating_value', 'engine_stroke',
                'engine_capacity', 'min_engine_on_speed', 'fmep_model', 'times',
                'phases_integration_times', 'engine_speeds_out',
                'engine_powers_out', 'velocities', 'accelerations',
                'motive_powers', 'engine_coolant_temperatures',
                'missing_powers', 'angle_slopes'],
        outputs=['phases_willans_factors'],
        input_domain=lambda *args: args[0]
    )

    d.add_function(
        function=calculate_optimal_efficiency,
        inputs=['co2_params_calibrated', 'mean_piston_speeds'],
        outputs=['optimal_efficiency']
    )

    d.add_function(
        function=calibrate_co2_params_v1,
        inputs=['co2_emissions_model', 'fuel_consumptions',
                'fuel_carbon_content', 'co2_params_initial_guess'],
        outputs=['co2_params_calibrated', 'calibration_status']
    )

    d.add_function(
        function=calculate_phases_fuel_consumptions,
        inputs=['phases_co2_emissions', 'fuel_carbon_content', 'fuel_density'],
        outputs=['phases_fuel_consumptions']
    )

    return d
