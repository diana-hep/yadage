import logging
import adage
import adage.node
import jsonpointer
import time
import uuid
import jsonpath_rw
from yadagestep import initstep
log = logging.getLogger(__name__)

def convert(thing):
    if type(thing)==jsonpath_rw.jsonpath.Index:
        return thing.index
    if type(thing)==jsonpath_rw.jsonpath.Fields:
        fs = thing.fields
        assert len(fs)==1
        return fs[0]

def unravelpath(path):
    if type(path)==jsonpath_rw.jsonpath.Child:
        for x in unravelpath(path.left):
            yield x
        yield convert(path.right)
    else:
        yield convert(path)

def path2pointer(path):
    return jsonpointer.JsonPointer.from_parts(x for x in unravelpath(path)).path

def checkmeta(flowview,metainfo):
    applied_ids = [rl.identifier for rl in flowview.applied_rules]
    rulesok = all([x in applied_ids for x in metainfo['rules']])
    stepsok = all([flowview.dag.getNode(x).has_result for x in metainfo['steps']])
    return (rulesok and stepsok)


def scope_done(scope,flowview):
    result = True
    
    bookkeeper = jsonpointer.JsonPointer(scope).resolve(flowview.bookkeeper)
    for k,v in bookkeeper.iteritems():
        for k,v in bookkeeper.iteritems():
            if k=='_meta':
                result = result and checkmeta(flowview,v)
            else:
                childscope = scope+'/{}'.format(k)
                result = result and scope_done(childscope,flowview)
    return result

################

class stage_base(object):
    def __init__(self,name,context,dependencies):
        self.name = name
        self.context = context
        self.dependencies = dependencies
        
    def applicable(self,flowview):
        for x in self.dependencies:
            depmatches = flowview.query(x,flowview.steps)
            
            if not depmatches:
                return False
            issubwork = '_nodeid' not in depmatches[0].value[0]
            if issubwork:
                if not all([scope_done(scope['_offset'],flowview) for match in depmatches for scope in match.value]):
                    return False
            else:
                if not all([x.has_result() for x in flowview.getSteps(x)]):
                    return False
        return True
        
    def apply(self,flowview):
        log.debug('applying stage: %s',self.name)
        self.view = flowview
        self.schedule()
        
    def addStep(self,step):
        dependencies = [self.view.dag.getNode(k.stepid) for k in step.inputs]
        self.view.addStep(step, stage = self.name, depends_on = dependencies)

    def addWorkflow(self,rules, initstep):
        self.view.addWorkflow(rules, initstep = initstep, stage = self.name)
 
class initStage(stage_base):
    def __init__(self, step, context, dependencies):
        super(initStage,self).__init__('init', context,dependencies)
        self.step = step
    
    def schedule(self):
        self.addStep(self.step)
    
    def json(self):
        return {'type':'initStage','info':'just init', 'name':self.name}
    
class jsonstage(stage_base):
    def __init__(self,json,context):
        self.stageinfo = json['scheduler']
        super(jsonstage,self).__init__(json['name'],context,json['dependencies']['expressions'])
        
    def schedule(self):
        from yadage.handlers.scheduler_handlers import handlers as sched_handlers
        scheduler = sched_handlers[self.stageinfo['scheduler_type']]
        scheduler(self,self.stageinfo)

    def json(self):
        return {'type':'jsonstage','info':self.stageinfo,'name':self.name}

class offsetRule(object):
    def __init__(self,rule,offset = None):
        self.identifier = str(uuid.uuid4())
        self.rule = rule
        self.offset = offset
    
    def applicable(self,adageobj):
        return self.rule.applicable(WorkflowView(adageobj,self.offset))
    
    def apply(self,adageobj):
        self.rule.apply(WorkflowView(adageobj,self.offset))
        
    def json(self):
        return {'type':'offset','offset':self.offset,'rule':self.rule.json(),'id':self.identifier}

class YadageNode(adage.node.Node):
    def __init__(self,name,task,identifier = None):
        super(YadageNode,self).__init__(name,task,identifier)
    
    def __repr__(self):
        lifetime = time.time()-self.define_time
        return '<YadageNode {} {} lifetime: {} (id: {})>'.format(self.name,self.state,lifetime,self.identifier)

    def has_result(self):
        # return self.successful()
        return (self.task.prepublished is not None) or self.successful()
    
    @property
    def result(self):
        if self.task.prepublished:
            return self.task.prepublished
        return super(YadageNode,self).result

class YadageWorkflow(adage.adageobject):
    def __init__(self):
        super(YadageWorkflow,self).__init__()
        self.stepsbystage = {}
        self.bookkeeping = {'_meta':{'rules':[],'steps':[]}}
        
    def view(self,offset = ''):
        return WorkflowView(self,offset)
        
    @classmethod
    def fromJSON(cls,jsondata,context):
        instance = cls()
        rules = [jsonstage(yml,context) for yml in jsondata['stages']]
        rootview = WorkflowView(instance)
        rootview.addWorkflow(rules)
        return instance

class WorkflowView(object):
    def __init__(self,workflowobj,offset = ''):
        self.dag           = workflowobj.dag
        self.rules         = workflowobj.rules
        self.applied_rules = workflowobj.applied_rules
        
        self.offset        = offset
        self.steps         = jsonpointer.JsonPointer(self.offset).resolve(workflowobj.stepsbystage)
        self.bookkeeper    = jsonpointer.JsonPointer(self.offset).resolve(workflowobj.bookkeeping)
        
        
    def query(self,query,collection):
        matches = jsonpath_rw.parse(query).find(collection)
        return matches
        
    def getSteps(self,query):
        result =  [self.dag.getNode(step['_nodeid']) for match in self.query(query,self.steps) for step in match.value]
        return result
        
    def init(self, initdata, name = 'init'):
        step = initstep(name,initdata)
        self.addRule(initStage(step,{},[]),self.offset)
            
    def addRule(self,rule,offset = ''):
        
        thisoffset = jsonpointer.JsonPointer(offset)
        if self.offset:
            fulloffset = jsonpointer.JsonPointer.from_parts(jsonpointer.JsonPointer(self.offset).parts + thisoffset.parts).path
        else:
            fulloffset = thisoffset.path
        offsetrule = offsetRule(rule,fulloffset)
        self.rules += [offsetrule]
        thisoffset.resolve(self.bookkeeper)['_meta']['rules'] += [offsetrule.identifier]
        return offsetrule.identifier
    
    def addStep(self,step, stage, depends_on = None):
        node = YadageNode(step.name,step)
        self.dag.addNode(node,depends_on = depends_on)
        
        noderef = {'_nodeid':node.identifier}
        if stage in self.steps:
            self.steps[stage] += [noderef]
        else:
            self.steps[stage]  = [noderef]
        self.bookkeeper['_meta']['steps'] += [node.identifier]

    def addWorkflow(self, rules, initstep = None, stage = None):
        if initstep:
            rules += [initStage(initstep,{},[])]
        
        newsteps = {}
        if stage in self.steps:
            self.steps[stage] += [newsteps]
        elif stage is not None:
            self.steps[stage]  = [newsteps]

        offset = jsonpointer.JsonPointer.from_parts([stage,len(self.steps[stage])-1]).path if stage else ''
        if stage is not None:
            self.steps[stage][-1]['_offset'] = offset
        
        booker = self.bookkeeper
        for p in jsonpointer.JsonPointer(offset).parts:
            if p in booker:
                pass
            else:
                booker[p] = {'_meta':{'rules':[],'steps':[]}}
            booker = booker[p]
        
        for rule in rules:
            self.addRule(rule,offset)
        
        