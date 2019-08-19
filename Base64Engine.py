import AlteryxPythonSDK as Sdk
import xml.etree.ElementTree as Et
import base64

class AyxPlugin:
    """
    Implements the plugin interface methods, to be utilized by the Alteryx engine to communicate with a plugin.
    Prefixed with "pi", the Alteryx engine will expect the below five interface methods to be defined.
    """

    def __init__(self, n_tool_id: int, alteryx_engine: object, output_anchor_mgr: object):
        """
        Constructor is called whenever the Alteryx engine wants to instantiate an instance of this plugin.
        :param n_tool_id: The assigned unique identification for a tool instance.
        :param alteryx_engine: Provides an interface into the Alteryx engine.
        :param output_anchor_mgr: A helper that wraps the outgoing connections for a plugin.
        """

        # Default properties
        self.n_tool_id = n_tool_id
        self.alteryx_engine = alteryx_engine
        self.output_anchor_mgr = output_anchor_mgr

        # Custom properties
        self.is_initialized = True
        self.single_input = None
        self.output_anchor = None
        self.output_field = None # encoded field
        self.input_field = None # field selected to encode

    def pi_init(self, str_xml: str):
        """
        Handles configuration based on the GUI.
        Called when the Alteryx engine is ready to provide the tool configuration from the GUI.
        :param str_xml: The raw XML from the GUI.
        """

        # Getting the dataName data property from the Gui.html
        self.field_selection = Et.fromstring(str_xml).find('EncodeField').text if 'EncodeField' in str_xml else None
        self.method = Et.fromstring(str_xml).find('Method').text if 'Method' in str_xml else None
        self.encoding_method = Et.fromstring(str_xml).find('EncodingMethod').text if 'EncodingMethod' in str_xml else None

        # Getting the output anchor from Config.xml by the output connection name
        self.output_anchor = self.output_anchor_mgr.get_output_anchor('Output')

    def pi_add_incoming_connection(self, str_type: str, str_name: str):
        """
        The IncomingInterface objects are instantiated here, one object per incoming connection.
        Called when the Alteryx engine is attempting to add an incoming data connection.
        :param str_type: The name of the input connection anchor, defined in the Config.xml file.
        :param str_name: The name of the wire, defined by the workflow author.
        :return: The IncomingInterface object(s).
        """

        self.single_input = IncomingInterface(self)
        
        return self.single_input

    def pi_add_outgoing_connection(self, str_name: str):
        """
        Called when the Alteryx engine is attempting to add an outgoing data connection.
        :param str_name: The name of the output connection anchor, defined in the Config.xml file.
        :return: True signifies that the connection is accepted.
        """

        return True

    def pi_push_all_records(self, n_record_limit: int):
        """
        Called when a tool has no incoming data connection.
        :param n_record_limit: Set it to <0 for no limit, 0 for no records, and >0 to specify the number of records.
        :return: True for success, False for failure.
        """

        self.alteryx_engine.output_message(self.n_tool_id, 
                                        Sdk.EngineMessageType.error, 
                                        "Missing Incoming Connection")

        return False

    def pi_close(self, b_has_errors: bool):
        """
        Called after all records have been processed..
        :param b_has_errors: Set to true to not do the final processing.
        """

        # Checks whether connections were properly closed.
        self.output_anchor.assert_close()

class IncomingInterface:
    """
    This class is returned by pi_add_incoming_connection, and it implements the incoming interface methods, to be
    utilized by the Alteryx engine to communicate with a plugin when processing an incoming connection.
    Prefixed with "ii", the Alteryx engine will expect the below four interface methods to be defined.
    """

    def __init__(self, parent: object):
        """
        Constructor for IncomingInterface.
        :param parent: AyxPlugin
        """
        # Default properties
        self.parent = parent

        # Custom properties
        self.record_copier = None
        self.record_creator = None

    def ii_init(self, record_info_in: object):
        """
        Handles appending the new field to the incoming data.
        Called to report changes of the incoming connection's record metadata to the Alteryx engine.
        :param record_info_in: A RecordInfo object for the incoming connection's fields.
        :return: False if there's an error with the field name, otherwise True.
        """

        if not self.parent.is_initialized:
            return False

        if not self.parent.field_selection:
            self.parent.alteryx_engine.output_message(self.parent.n_tool_id, 
                                                    Sdk.EngineMessageType.error, 
                                                    "Select a Field to "+str(self.parent.method))
            return False
        else:
            # Encoded/decoded column name and description
            altered_column_name = str(self.parent.field_selection)+"_"+str(self.parent.method)
            altered_description = str(self.parent.encoding_method)+" "+str(self.parent.method)

        # Returns a new, empty RecordCreator object that is identical to record_info_in.
        record_info_out = record_info_in.clone()

        # Adds field to record with specified name and output type.
        record_info_out.add_field(altered_column_name, Sdk.FieldType.v_wstring, 1073741823, 0, str(altered_description))

        # Lets the downstream tools know what the outgoing record metadata will look like, based on record_info_out.
        self.parent.output_anchor.init(record_info_out)

        # Creating a new, empty record creator based on record_info_out's record layout.
        self.record_creator = record_info_out.construct_record_creator()

        # Instantiate a new instance of the RecordCopier class.
        self.record_copier = Sdk.RecordCopier(record_info_out, record_info_in)

        # Map each column of the input to where we want in the output.
        for index in range(record_info_in.num_fields):
            # Adding a field index mapping.
            self.record_copier.add(index, index)

        # Let record copier know that all field mappings have been added.
        self.record_copier.done_adding()

        # Grab the index of our new field in the record, so we don't have to do a string lookup on every push_record.
        self.parent.output_field = record_info_out[record_info_out.get_field_num(altered_column_name)]
        self.parent.input_field = record_info_out[record_info_out.get_field_num(self.parent.field_selection)]
        
        return True

    def ii_push_record(self, in_record: object):
        """
        Responsible for pushing records out, under a count limit set by the user in n_record_select.
        Called when an input record is being sent to the plugin.
        :param in_record: The data for the incoming record.
        :return: False if method calling limit (record_cnt) is hit.
        """
        if not self.parent.is_initialized:
            return False

        # Copy the data from the incoming record into the outgoing record.
        self.record_creator.reset()
        self.record_copier.copy(self.record_creator, in_record)

        if self.parent.method == 'encode':
            # Encode the selected field's data
            altered_field_data = self.encode_data(in_record)
        else:
            # Decode the selected field's data
            altered_field_data = self.decode_data(in_record)

        # Sets the value of this field in the specified record_creator from an int64 value.
        self.parent.output_field.set_from_string(self.record_creator, altered_field_data)

        # Finalize the outgoing record
        out_record = self.record_creator.finalize_record()

        # Push the record downstream and quit if there's a downstream error.
        if not self.parent.output_anchor.push_record(out_record):
            return False

        return True

    def ii_update_progress(self, d_percent: float):
        """
        Called by the upstream tool to report what percentage of records have been pushed.
        :param d_percent: Value between 0.0 and 1.0.
        """

        # Inform the Alteryx engine of the tool's progress.
        self.parent.alteryx_engine.output_tool_progress(self.parent.n_tool_id, d_percent)

        # Inform the outgoing connections of the tool's progress.
        self.parent.output_anchor.update_progress(d_percent)

    def ii_close(self):
        """
        Called when the incoming connection has finished passing all of its records.
        """

        # Close outgoing connections.
        self.parent.output_anchor.close()

    def encode_data(self, in_record: object):
        """
        Non-Incoming Interface function that's used to encodes the 
        selected field data using the base64 library method specified
        """

        # Access the data associated with the selected field
        field_data_original = self.parent.input_field.get_as_string(in_record)
        
        # Encode original data as bytes
        field_data_bytes = field_data_original.encode('utf-8')
        
        # Enode the field_data_bytes as specified
        if self.parent.encoding_method == "b64_standard":
            field_data_encoded = base64.standard_b64encode(field_data_bytes)            
        elif self.parent.encoding_method == "b64_url_safe":
            field_data_encoded = base64.urlsafe_b64encode(field_data_bytes)            
        elif self.parent.encoding_method == "b32":
            field_data_encoded = base64.b32encode(field_data_bytes)            
        elif self.parent.encoding_method == "b16":
            field_data_encoded = base64.b16encode(field_data_bytes)            
        else:
            # Default to outputting the original data if no method selected
            return(field_data_original)

        # Convert encoded data back to str - removes b'' wrapper
        field_data_encoded = str(field_data_encoded.decode('utf-8'))

        return(field_data_encoded)

    def decode_data(self, in_record: object):
        """
        Non-Incoming Interface function that's used to decode the 
        selected field data using the base64 library method specified
        """

        # Access the data associated with the selected field
        field_data_original = self.parent.input_field.get_as_string(in_record)
        
        # Encode original data as bytes
        field_data_bytes = field_data_original.encode('utf-8')
        
        # Enode the field_data_bytes as specified
        if self.parent.encoding_method == "b64_standard":
            field_data_decoded = base64.standard_b64decode(field_data_bytes)            
        elif self.parent.encoding_method == "b64_url_safe":
            field_data_decoded = base64.urlsafe_b64decode(field_data_bytes)            
        elif self.parent.encoding_method == "b32":
            field_data_decoded = base64.b32decode(field_data_bytes)            
        elif self.parent.encoding_method == "b16":
            field_data_decoded = base64.b16decode(field_data_bytes)            
        else:
            # Default to outputting the original data if no method selected
            return(field_data_original)

        # Convert encoded data back to str - removes b'' wrapper
        field_data_decoded = str(field_data_decoded.decode('utf-8'))

        return(field_data_decoded)