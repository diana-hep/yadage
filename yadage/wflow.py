import adage
from adage.serialize import obj_to_json

from .stages import JsonStage, OffsetStage
from .wflowview import WorkflowView
from .wflownode import YadageNode

from .backends import load_proxy


def json_or_nil(x):
    return None if x is None else x.json()

class YadageWorkflow(adage.adageobject):
    '''
    The overall workflow state object that extends the basic
    Adage state object by two bookkeeping structures.
    '''

    def __init__(self, dag = None, rules = None, applied_rules = None, bookkeeping = None, stepsbystage = None, values = None):
        super(YadageWorkflow, self).__init__(
            dag = dag,
            rules = rules,
            applied_rules = applied_rules
        )
        self.stepsbystage = stepsbystage or {}
        self.bookkeeping = bookkeeping or {}
        self.values = values or {}

    def view(self, offset=''):
        return WorkflowView(self, offset)

    def json(self):
        data = obj_to_json(self,
                           ruleserializer=json_or_nil,
                           taskserializer=json_or_nil,
                           proxyserializer=json_or_nil,
                           )
        data['bookkeeping'] = self.bookkeeping
        data['stepsbystage'] = self.stepsbystage
        data['values'] = self.values
        return data

    @classmethod
    def fromJSON(cls, data,deserialization_opts = None,backend=None):

        def node_deserializer(data):
            node = YadageNode.fromJSON(data,deserialization_opts)
            adage.serialize.set_generic_data(node,data)
            node.resultproxy = load_proxy(data['proxy'],deserialization_opts) if data['proxy'] else None
            if backend:
                node.backend = backend
                node.update_state()
            return node

        def rule_deserializer(data):
            return OffsetStage.fromJSON(data,deserialization_opts)

        dag = adage.serialize.dag_from_json(
                    data['dag'],
                    node_deserializer
                )

        instance = cls(dag = dag,
            rules = [rule_deserializer(x) for x in data['rules'] ],
            applied_rules = [rule_deserializer(x) for x in data['applied'] ],
            bookkeeping = data['bookkeeping'],
            stepsbystage = data['stepsbystage'],
            values = data['values']
        )

        return instance

    @classmethod
    def createFromJSON(cls, jsondata, state_provider):
        instance = cls()
        rules = [JsonStage(stagedata, state_provider) for stagedata in jsondata['stages']]
        instance.view().addWorkflow(rules)
        return instance
