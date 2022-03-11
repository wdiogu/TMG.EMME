# ---LICENSE----------------------
"""
    Copyright 2022 Travel Modelling Group, Department of Civil Engineering, University of Toronto

    This file is part of the TMG Toolbox.

    The TMG Toolbox is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    The TMG Toolbox is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with the TMG Toolbox.  If not, see <http://www.gnu.org/licenses/>.
"""

# ---METADATA---------------------
"""
TMG Transit Assignment Tool
    Executes a multi-class congested transit assignment procedure for GTAModel V4.0+. 

    Hard-coded assumptions: 
    -  Boarding penalties are assumed stored in UT3
    -  The congestion term is stored in US3
    -  In-vehicle time perception is 1.0
    -  Unless specified, all available transit modes will be used.
    
    This tool is only compatible with Emme 4.2 and later versions

    Authors: Eric Miller

    Latest revision by: WilliamsDiogu
    
    Executes a transit assignment which allows for surface transit speed updating.
    
    V 1.0.0 

    V 2.0.0 Refactored to work with XTMF2/TMGToolbox2 on 2021-12-15 by williamsDiogu   

    V 2.0.1 Updated to receive JSON object parameters from XTMX2

    V 2.0.2 Updated to receive JSON file parameters from Python API call
"""
import enum
import traceback as _traceback
import time as _time
import multiprocessing
from typing import DefaultDict
import inro.modeller as _m
from contextlib import contextmanager

_m.TupleType = object
_m.ListType = list
_m.InstanceType = object
_trace = _m.logbook_trace
_write = _m.logbook_write
_MODELLER = _m.Modeller()
_bank = _MODELLER.emmebank
_util = _MODELLER.module("tmg2.utilities.general_utilities")
_tmg_tpb = _MODELLER.module("tmg2.utilities.TMG_tool_page_builder")
network_calc_tool = _MODELLER.tool("inro.emme.network_calculation.network_calculator")
extended_assignment_tool = _MODELLER.tool(
    "inro.emme.transit_assignment.extended_transit_assignment"
)
null_pointer_exception = _util.null_pointer_exception
EMME_VERSION = _util.get_emme_version(tuple)


class AssignTransit(_m.Tool()):
    version = "2.0.0"
    tool_run_msg = ""
    number_of_tasks = 15

    def __init__(self):
        self._tracker = _util.progress_tracker(self.number_of_tasks)
        self.scenario = _MODELLER.scenario
        self.number_of_processors = multiprocessing.cpu_count()
        self.connector_logit_truncation = 0.05
        self.consider_total_impedance = True
        self.use_logit_connector_choice = True

    def page(self):
        if EMME_VERSION < (4, 1, 5):
            raise ValueError("Tool not compatible. Please upgrade to version 4.1.5+")
        pb = _tmg_tpb.TmgToolPageBuilder(
            self,
            title="Multi-Class Transit Assignment v%s" % self.version,
            description="Executes a congested transit assignment procedure\
                        for GTAModel V4.0.\
                        <br><br><b>Cannot be called from Modeller.</b>\
                        <br><br>Hard-coded assumptions:\
                        <ul><li> Boarding penalties are assumed stored in <b>UT3</b></li>\
                        <li> The congestion term is stored in <b>US3</b></li>\
                        <li> In-vehicle time perception is 1.0</li>\
                        <li> All available transit modes will be used.</li>\
                        </ul>\
                        <font color='red'>This tool is only compatible with Emme 4.1.5 and later versions</font>",
            runnable=False,
            branding_text="- TMG Toolbox",
        )
        return pb.render()

    def __call__(self, parameters):
        scenario = _util.load_scenario(parameters["scenario_number"])
        try:
            self._execute(scenario, parameters)
        except Exception as e:
            raise Exception(_util.format_reverse_stack())

    def run_xtmf(self, parameters):
        scenario = _util.load_scenario(parameters["scenario_number"])
        self._check_attributs_exists(scenario, parameters)
        try:
            self._execute(scenario, parameters)
        except Exception as e:
            raise Exception(_util.format_reverse_stack())

    def _execute(self, scenario, parameters):
        load_input_matrix_list = self._load_input_matrices(parameters, "demand_matrix")
        load_output_matrix_dict = self._load_output_matrices(
            parameters,
            matrix_name=[
                "in_vehicle_time_matrix",
                "congestion_matrix",
                "walk_time_matrix",
                "wait_time_matrix",
                "fare_matrix",
                "board_penalty_matrix",
            ],
        )
        with _trace(
            name="(%s v%s)" % (self.__class__.__name__, self.version),
            attributes=self._load_atts(scenario, parameters),
        ):
            self._tracker.reset()
            with _trace("Checking travel time functions..."):
                changes = self._heal_travel_time_functions()
                if changes == 0:
                    _write("No problems were found")
            with _util.temporary_matrix_manager() as temp_matrix_list:
                # Initialize matrices with matrix ID = "mf0" not loaded in load_input_matrix_list
                demand_matrix_list = self._init_input_matrices(
                    load_input_matrix_list, temp_matrix_list
                )
                in_vehicle_time_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="in_vehicle_time_matrix",
                    description="Transit in-vehicle travel times.",
                )
                congestion_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="congestion_matrix",
                    description="Transit in-vehicle congestion.",
                )
                walk_time_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="walk_time_matrix",
                    description="Transit total walk times.",
                )
                wait_time_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="wait_time_matrix",
                    description="Transit total wait times.",
                )
                fare_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="fare_matrix",
                    description="Transit total fares",
                )
                board_penalty_matrix_list = self._init_output_matrices(
                    load_output_matrix_dict,
                    temp_matrix_list,
                    matrix_name="board_penalty_matrix",
                    description="Transit total boarding penalties",
                )
                impedance_matrix_list = self._get_impedance_matrices(
                    parameters, temp_matrix_list
                )
                self._change_walk_speed(scenario, parameters["walk_speed"])
                with _util.temporary_attribute_manager(scenario) as temp_attribute_list:
                    effective_headway_attribute_list = (
                        self._create_headway_attribute_list(
                            scenario,
                            "TRANSIT_LINE",
                            temp_attribute_list,
                            default_value=0.0,
                            hdw_att_name=parameters["effective_headway_attribute"],
                        )
                    )
                    headway_fraction_attribute_list = (
                        self._create_headway_attribute_list(
                            scenario,
                            "NODE",
                            temp_attribute_list,
                            default_value=0.5,
                            hdw_att_name=parameters["headway_fraction_attribute"],
                        )
                    )
                    walk_time_perception_attribute_list = (
                        self._create_walk_time_perception_attribute_list(
                            scenario, parameters, temp_attribute_list
                        )
                    )
                    self._tracker.start_process(5)
                    self._assign_effective_headway(
                        scenario,
                        parameters,
                        effective_headway_attribute_list[0].id,
                    )
                    self._tracker.complete_subtask()
                    self._assign_walk_perception(scenario, parameters)
                    if parameters["node_logit_scale"] is not False:
                        self._publish_efficient_connector_network(scenario)
                    with _util.temp_extra_attribute_manager(
                        scenario, "TRANSIT_LINE"
                    ) as stsu_att:
                        with self._temp_stsu_ttfs(scenario, parameters) as ttf_map:
                            if parameters["surface_transit_speed"] != False:
                                pass
                            self._run_transit_assignment(
                                scenario,
                                parameters,
                                stsu_att,
                                demand_matrix_list,
                                effective_headway_attribute_list,
                                headway_fraction_attribute_list,
                                impedance_matrix_list,
                                walk_time_perception_attribute_list,
                            )

    # ---LOAD - SUB FUNCTIONS -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _load_atts(self, scenario, parameters):
        # TODO
        atts = {}
        return atts

    def _check_attributs_exists(self, scenario, parameters):
        walk_att = "walk_time_perception_attribute"
        seg_att = "segment_fare_attribute"
        ehwy_att = "effective_headway_attribute"
        hwy_att = "headway_fraction_attribute"
        link_att = "link_fare_attribute_id"
        for tc in parameters["transit_classes"]:
            if scenario.extra_attribute(tc[walk_att]) is None:
                raise Exception(
                    "Walk perception attribute %s does not exist" % walk_att
                )
            if scenario.extra_attribute(tc[seg_att]) is None:
                raise Exception("Segment fare attribute %s does not exist" % seg_att)
            if scenario.extra_attribute(tc[link_att]) is None:
                raise Exception("Link fare attribute %s does not exist" % link_att)
        if scenario.extra_attribute(parameters[ehwy_att]) is None:
            raise Exception("Effective headway attribute %s does not exist" % ehwy_att)
        if scenario.extra_attribute(parameters[hwy_att]) is None:
            raise Exception("Effective headway attribute %s does not exist" % hwy_att)

    # ---INITIALIZE - SUB-FUNCTIONS  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    def _load_output_matrices(self, parameters, matrix_name=[]):
        """
        This loads all (into a dictionary) output matrices by matrix_name list provided but
        assigns None to all zero matrices for later initialization
        """
        mtx_dict = {}
        transit_classes = parameters["transit_classes"]
        for i in range(0, len(matrix_name)):
            mtx_dict[matrix_name[i]] = [tc[matrix_name[i]] for tc in transit_classes]
        for mtx_name, mtx_ids in mtx_dict.items():
            mtx = [None if id == "mf0" else _bank.matrix(id) for id in mtx_ids]
            mtx_dict[mtx_name] = mtx
        return mtx_dict

    def _load_input_matrices(self, parameters, matrix_name):
        """
        Load input matrices creates and loads all (input) matrix into a list based on
        matrix_name supplied. E.g of matrix_name: "demand_matrix" and matrix_id: "mf2"
        """

        def exception(mtx_id):
            raise Exception("Matrix %s was not found!" % mtx_id)

        transit_classes = parameters["transit_classes"]
        mtx_list = [
            _bank.matrix(tc[matrix_name])
            if tc[matrix_name] == "mf0" or _bank.matrix(tc[matrix_name]) is not None
            else exception(tc[matrix_name])
            for tc in transit_classes
        ]
        return mtx_list

    def _init_input_matrices(self, load_input_matrix_list, temp_matrix_list):
        input_matrix_list = []
        for mtx in load_input_matrix_list:
            if mtx == None:
                mtx = _util.initialize_matrix(matrix_type="FULL")
                input_matrix_list.append(_bank.matrix(mtx.id))
                temp_matrix_list.append(mtx)
            else:
                input_matrix_list.append(mtx)
        return input_matrix_list

    def _get_impedance_matrices(self, parameters, temp_matrix_list):
        """
        Creates temporary matrix for matrices with id = "mf0"
        """
        impedance_matrix_list = []
        transit_classes = parameters["transit_classes"]
        for tc_parameter in transit_classes:
            matrix_id = tc_parameter["impedance_matrix"]
            if matrix_id != "mf0":
                _util.initialize_matrix(
                    id=matrix_id,
                    description="Transit Perceived Travel times for %s"
                    % tc_parameter["name"],
                )
                impedance_matrix_list.append(matrix)
            else:
                _write(
                    "Creating temporary Impedance Matrix for class %s"
                    % tc_parameter["name"]
                )
                matrix = _util.initialize_matrix(
                    default=0.0,
                    description="Temporary Impedance for class %s"
                    % tc_parameter["name"],
                    matrix_type="FULL",
                )
                impedance_matrix_list.append(matrix)
                temp_matrix_list.append(matrix)
        return impedance_matrix_list

    def _init_output_matrices(
        self,
        load_output_matrix_dict,
        temp_matrix_list,
        matrix_name="",
        description="",
    ):
        """
        Initializes all output matrices provided.
        """
        output_matrix_list = []
        desc = "TRANSIT %s FOR CLASS" % (matrix_name.upper())
        if matrix_name in load_output_matrix_dict.keys():
            for mtx in load_output_matrix_dict[matrix_name]:
                if mtx != None:
                    matrix = _util.initialize_matrix(
                        name=matrix_name,
                        description=description if description != "" else desc,
                    )
                    output_matrix_list.append(matrix)
                else:
                    if matrix_name == "impedance_matrix":
                        _write('Creating Temporary Impedance Matrix "%s"', matrix_name)
                        matrix = _util.initialize_matrix(
                            default=0.0,
                            description=description if description != "" else desc,
                            matrix_type="FULL",
                        )
                        output_matrix_list.append(matrix)
                        temp_matrix_list.append(matrix)
                    else:
                        output_matrix_list.append(mtx)
        else:
            raise Exception(
                'Output matrix name "%s" provided does not exist', matrix_name
            )
        return output_matrix_list

    def _heal_travel_time_functions(self):
        changes = 0
        for function in _bank.functions():
            if function.type != "TRANSIT_TIME":
                continue
            cleaned_expression = function.expression.replace(" ", "")
            if "us3" in cleaned_expression:
                if cleaned_expression.endswith("*(1+us3)"):
                    index = cleaned_expression.find("*(1+us3)")
                    new_expression = cleaned_expression[:index]
                    function.expression = new_expression
                    print(
                        "Detected function %s with existing congestion term." % function
                    )
                    print("Original expression= '%s'" % cleaned_expression)
                    print("Healed expression= '%s'" % new_expression)
                    print("")
                    _write(
                        "Detected function %s with existing congestion term." % function
                    )
                    _write("Original expression= '%s'" % cleaned_expression)
                    _write("Healed expression= '%s'" % new_expression)
                    changes += 1
                else:
                    raise Exception(
                        "Function %s already uses US3, which is reserved for transit"
                        % function
                        + " segment congestion values. Please modify the expression "
                        + "to use different attributes."
                    )
        return changes

    def _change_walk_speed(self, scenario, walk_speed):
        with _trace("Setting walk speeds to %s" % walk_speed):
            partial_network = scenario.get_partial_network(["MODE"], True)
            for mode in partial_network.modes():
                if mode.type != "AUX_TRANSIT":
                    continue
                mode.speed = walk_speed
                _write("Changed mode %s" % mode.id)
            baton = partial_network.get_attribute_values("MODE", ["speed"])
            scenario.set_attribute_values("MODE", ["speed"], baton)

    def _create_walk_time_perception_attribute_list(
        self, scenario, parameters, temp_matrix_list
    ):
        walk_time_perception_attribute_list = []
        for tc_parameter in parameters["transit_classes"]:
            walk_time_perception_attribute = _util.create_temp_attribute(
                scenario,
                str(tc_parameter["walk_time_perception_attribute"]),
                "LINK",
                default_value=1.0,
                assignment_type="transit",
            )
            walk_time_perception_attribute_list.append(walk_time_perception_attribute)
            temp_matrix_list.append(walk_time_perception_attribute)
        return walk_time_perception_attribute_list

    def _create_headway_attribute_list(
        self,
        scenario,
        attribute_type,
        temp_matrix_list,
        default_value=0.0,
        hdw_att_name="",
    ):
        headway_attribute_list = []
        headway_attribute = _util.create_temp_attribute(
            scenario,
            str(hdw_att_name),
            str(attribute_type),
            default_value=default_value,
            assignment_type="transit",
        )
        headway_attribute_list.append(headway_attribute)
        temp_matrix_list.append(headway_attribute)
        return headway_attribute_list

    def _assign_effective_headway(
        self, scenario, parameters, effective_headway_attribute_id
    ):
        small_headway_spec = {
            "result": effective_headway_attribute_id,
            "expression": "hdw",
            "aggregation": None,
            "selections": {"transit_line": "hdw=0,15"},
            "type": "NETWORK_CALCULATION",
        }
        large_headway_spec = {
            "result": effective_headway_attribute_id,
            "expression": "15+2*"
            + str(parameters["effective_headway_slope"])
            + "*(hdw-15)",
            "aggregation": None,
            "selections": {"transit_line": "hdw=15,999"},
            "type": "NETWORK_CALCULATION",
        }
        network_calc_tool(small_headway_spec, scenario)
        network_calc_tool(large_headway_spec, scenario)

    def _assign_walk_perception(self, scenario, parameters):
        transit_classes = parameters["transit_classes"]
        for tc in transit_classes:
            walk_time_perception_attribute = tc["walk_time_perception_attribute"]
            ex_att = scenario.extra_attribute(walk_time_perception_attribute)
            ex_att.initialize(1.0)

        def apply_selection(val, selection):
            spec = {
                "result": walk_time_perception_attribute,
                "expression": str(val),
                "aggregation": None,
                "selections": {"link": selection},
                "type": "NETWORK_CALCULATION",
            }
            network_calc_tool(spec, scenario)

        with _trace("Assigning perception factors"):
            for tc in transit_classes:
                for wp in tc["walk_perceptions"]:
                    selection = str(wp["filter"])
                    value = str(wp["walk_perception_value"])
                    apply_selection(value, selection)

    def _publish_efficient_connector_network(self, scenario):
        """
        Creates a network that completely replaces the scenario network in memory/disk, with
        one that allows for the use of a logit distribution at specified choice points.

        Run:
            - set "node_logit_scale" parameter = TRUE, to run Logit Discrete Choice Model
            - set "node_logit_scale" parameter = FALSE, to run Optimal Strategy Transit Assignment

            ** This method only runs when node logit scale is not FALSE

        Args:
            - scenario: The Emme Scenario object to load network from and to

        Implementation Notes:
            - Regular nodes that are centroids are used as choice points:

                ** Node attributes are set to -1 to apply logit distribution to efficient connectors
                   (connectors that bring travellers closer to destination) only. Setting node attributes
                   to 1 apply same to all connectors.

                    *** Outgoing link connector attributes must be set to -1 to override flow connectors with fixed proportions.

        """
        network = scenario.get_network()
        for node in network.regular_nodes():
            node.data1 = 0
        for node in network.regular_nodes():
            agency_counter = 0
            if node.number > 99999:
                continue
            for link in node.incoming_links():
                if link.i_node.is_centroid is True:
                    node.data1 = -1
                if link.i_node.number > 99999:
                    agency_counter += 1
            for link in node.outgoing_links():
                if link.j_node.is_centroid is True:
                    node.data1 = -1
            if agency_counter > 1:
                node.data1 = -1
                for link in node.incoming_links():
                    if link.i_node.number > 99999:
                        link.i_node.data1 = -1
                for link in node.outgoing_links():
                    if link.j_node.number > 99999:
                        link.j_node.data1 = -1
        scenario.publish_network(network)

    def _run_transit_assignment(
        self,
        scenario,
        parameters,
        stsu_att,
        demand_matrix_list,
        effective_headway_attribute_list,
        headway_fraction_attribute_list,
        impedance_matrix_list,
        walk_time_perception_attribute_list,
    ):
        if parameters["congested_assignment"] == True:
            pass
        else:
            self._run_uncongested_assignment(
                scenario,
                parameters,
                stsu_att,
                demand_matrix_list,
                effective_headway_attribute_list,
                headway_fraction_attribute_list,
                impedance_matrix_list,
                walk_time_perception_attribute_list,
            )

    def _run_uncongested_assignment(
        self,
        scenario,
        parameters,
        stsu_att,
        demand_matrix_list,
        effective_headway_attribute_list,
        headway_fraction_attribute_list,
        impedance_matrix_list,
        walk_time_perception_attribute_list,
    ):
        if parameters["surface_transit_speed"] == False:
            for i, tc in enumerate(parameters["transit_classes"]):
                spec_uncongested = self._get_base_assignment_spec_uncongested(
                    scenario,
                    tc["board_penalty_perception"],
                    self.connector_logit_truncation,
                    self.consider_total_impedance,
                    demand_matrix_list[i],
                    effective_headway_attribute_list[i],
                    tc["fare_perception"],
                    headway_fraction_attribute_list[i],
                    impedance_matrix_list[i],
                    tc["link_fare_attribute_id"],
                    [tc["mode"]],
                    parameters["node_logit_scale"],
                    self.number_of_processors,
                    parameters["origin_distribution_logit_scale"],
                    tc["segment_fare_attribute"],
                    self.use_logit_connector_choice,
                    tc["wait_time_perception"],
                    parameters["walk_all_way_flag"],
                    walk_time_perception_attribute_list[i],
                )
                self._tracker.run_tool(
                    extended_assignment_tool,
                    specification=spec_uncongested,
                    class_name=tc["name"],
                    scenario=scenario,
                    add_volumes=(i != 0),
                )
        else:
            pass

    def _get_base_assignment_spec_uncongested(
        self,
        scenario,
        board_perception,
        connector_logit_truncation,
        consider_total_impedance,
        demand_matrix,
        effective_headway,
        fare_perception,
        headway_fraction,
        impedance_matrix,
        link_fare_attribute,
        modes,
        node_logit_scale,
        number_of_processors,
        origin_distribution_logit_scale,
        segment_fare,
        use_logit_connector_choice,
        wait_perception,
        walk_all_way_flag,
        walk_attribute,
    ):
        if fare_perception != 0.0:
            fare_perception = 60.0 / fare_perception
        base_spec = {
            "modes": modes,
            "demand": demand_matrix.id,
            "waiting_time": {
                "headway_fraction": headway_fraction.id,
                "effective_headways": effective_headway.id,
                "spread_factor": 1,
                "perception_factor": wait_perception,
            },
            "boarding_time": {
                "at_nodes": None,
                "on_lines": {
                    "penalty": "ut3",
                    "perception_factor": board_perception,
                },
            },
            "boarding_cost": {
                "at_nodes": {"penalty": 0, "perception_factor": 1},
                "on_lines": None,
            },
            "in_vehicle_time": {"perception_factor": "us2"},
            "in_vehicle_cost": {
                "penalty": segment_fare,
                "perception_factor": fare_perception,
            },
            "aux_transit_time": {"perception_factor": walk_attribute.id},
            "aux_transit_cost": {
                "penalty": link_fare_attribute,
                "perception_factor": fare_perception,
            },
            "connector_to_connector_path_prohibition": None,
            "od_results": {"total_impedance": impedance_matrix.id},
            "flow_distribution_between_lines": {
                "consider_total_impedance": consider_total_impedance
            },
            "save_strategies": True,
            "type": "EXTENDED_TRANSIT_ASSIGNMENT",
        }
        if use_logit_connector_choice:
            base_spec["flow_distribution_at_origins"] = {
                "choices_at_origins": {
                    "choice_points": "ALL_ORIGINS",
                    "choice_set": "ALL_CONNECTORS",
                    "logit_parameters": {
                        "scale": origin_distribution_logit_scale,
                        "truncation": connector_logit_truncation,
                    },
                },
                "fixed_proportions_on_connectors": None,
            }
        base_spec["performance_settings"] = {
            "number_of_processors": number_of_processors
        }
        if node_logit_scale is not False:
            base_spec["flow_distribution_at_regular_nodes_with_aux_transit_choices"] = {
                "choices_at_regular_nodes": {
                    "choice_points": "ui1",
                    "aux_transit_choice_set": "ALL_POSSIBLE_LINKS",
                    "logit_parameters": {
                        "scale": node_logit_scale,
                        "truncation": connector_logit_truncation,
                    },
                }
            }
        else:
            base_spec["flow_distribution_at_regular_nodes_with_aux_transit_choices"] = {
                "choices_at_regular_nodes": "OPTIMAL_STRATEGY"
            }

        mode_list = []
        partial_network = scenario.get_partial_network(["MODE"], True)
        # if all modes are selected for class, get all transit modes for journey levels
        if modes == ["*"]:
            for mode in partial_network.modes():
                if mode.type == "TRANSIT":
                    mode_list.append({"mode": mode.id, "next_journey_level": 1})
        base_spec["journey_levels"] = [
            {
                "description": "Walking",
                "destinations_reachable": walk_all_way_flag,
                "transition_rules": mode_list,
                "boarding_time": None,
                "boarding_cost": None,
                "waiting_time": None,
            },
            {
                "description": "Transit",
                "destinations_reachable": True,
                "transition_rules": mode_list,
                "boarding_time": None,
                "boarding_cost": None,
                "waiting_time": None,
            },
        ]
        return base_spec

    @contextmanager
    def _temp_stsu_ttfs(self, scenario, parameters):
        orig_ttf_values = scenario.get_attribute_values(
            "TRANSIT_SEGMENT", ["transit_time_func"]
        )
        ttfs_changed = False
        _temp_stsu_map = {}
        created = {}
        for ttf in parameters["ttf_definitions"]:
            for i in range(1, 100):
                func = "ft" + str(i)
                if scenario.emmebank.function(func) is None:
                    scenario.emmebank.create_function(func, "(length*60/us1)")
                    _temp_stsu_map[int(ttf["ttf"])] = int(func[2:])
                    if str(ttf["ttf"]) in parameters["xrow_ttf_range"]:
                        parameters["xrow_ttf_range"].add(int(func[2:]))
                    created[func] = True
                    break
        try:
            yield _temp_stsu_map
        finally:
            for func in created:
                if created[func] == True:
                    scenario.emmebank.delete_function(func)
            if ttfs_changed == True:
                scenario.set_attribute_values(
                    "TRANSIT_SEGMENT", ["transit_time_func"], orig_ttf_values
                )

    @_m.method(return_type=_m.TupleType)
    def percent_completed(self):
        return self._tracker.get_progress()

    @_m.method(return_type=str)
    def tool_run_msg_status(self):
        return self.tool_run_msg