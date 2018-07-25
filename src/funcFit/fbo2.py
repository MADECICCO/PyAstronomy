# -*- coding: utf-8 -*-
from __future__ import print_function, division
import numpy as np
import re
import copy
import types
from PyAstronomy.pyaC import pyaErrors as PE
from PyAstronomy import pyaC
from .fufDS import FufDS
from .extFitter import NelderMead
import six
import six.moves as smo
import bidict
import collections
import sys
import scipy.optimize as sco
import inspect

from PyAstronomy.funcFit import _pymcImport, _scoImport, ic

if _pymcImport:
    import pymc
if _scoImport:
    import scipy.optimize as sco
if ic.check["emcee"]:
    import emcee
if ic.check["progressbar"]:
    import progressbar


class PyAPa(object):
    
    def setVal(self, v):
        self._value = v
    
    def getVal(self):
        return self._value
    
    value = property(getVal, setVal)
    
    def __init__(self, value=0.0):
        self._value = value
        self.free = False
        self.affects = set()
        self.relation = None
        self.dependsOn = None
        self.thawable = True
        
    def updateRelation(self):
        """
        Update value according to relation
        """
        if not self.relation is None:
            self._value = self.relation.update()

      
class PyARelation(object):
    
    def __init__(self, ivs, func):
        """
        Parameters
        ----------
        ivs : list of Parameter instances
            The independent parameters
        func : Callable
            The functional relation
        """
        self.ivs = ivs
        self.func = func
    
    def update(self):
        return self.func(*[p.value for p in self.ivs])
    
    
class PyAPrior(object):
    
    def __init__(self, ivs, func, descr=None):
        """
        Parameters
        ----------
        ivs : list of Parameter instances
            The independent parameters
        func : Callable
            Function returning log(prior) depending on the value of the
            independent parameters
        descr : string, optional
            Description of the prior
        """
        if not hasattr(ivs, "__iter__"):
            ivs = [ivs]
        self.ivs = ivs
        self.func = func
        if descr is None:
            self.descr = "Pya Prior"
        else:
            self.descr = descr
    
    def evaluate(self):
        return self.func(*[p.value for p in self.ivs])
    
    def __str__(self):
        return str(self.descr)


class PyAUniformPrior(PyAPrior):
    
    def __init__(self, ivs, lower=None, upper=None, descr=""):

        if (lower is None) and (upper is None):
            raise(PE.PyAValError("At least one of lower and upper must be specified"))

        if (lower is None) and (upper is None):
            f = lambda x:0.0
        elif (not lower is None) and (upper is None):
            f = lambda x:0.0 if x < upper else -np.inf
        elif (lower is None) and (not upper is None):
            f = lambda x:0.0 if x > lower else -np.inf
        else:
            if upper <= lower:
                raise(PE.PyAValError("'lower' must be smaller than 'upper'", \
                                     where="addUniformPrior"))
            r = upper - lower
            f = lambda x:np.log(r) if (x >= lower) and(x <=upper) else -np.inf
        
        PyAPrior.__init__(self, ivs, f, descr=descr)
      
      
class PyASmoothUniformPrior(PyAPrior):
      
    def __init__(self, ivs, lower=None, upper=None, scale=1e-9, descr=""):
      
        if (lower is None) and (upper is None):
            raise(PE.PyAValError("At least one of lower and upper must be specified"))      
      
        pih = np.pi/2.0
        flmax = sys.float_info.max
        def f(x):
            if not lower is None:
                if x < lower:
                    return -np.arctan((lower-x)/scale)/pih*flmax
            if not upper is None:
                if x > upper:
                    return -np.arctan((x-upper)/scale)/pih*flmax
            return 0.0
        
        PyAPrior.__init__(self, ivs, f, descr=descr)
      
      
class PyABPS(object):
    """
    Basic Parameter Set
    """
    
    def addParam(self, name, value=0.0):
        """
        Add parameter to the list
        
        Parameters
        ----------
        name : string
            Name of the parameter
        value : optional
            Default value of the parameter
        """
        if name in self.pmap:
            raise(PE.PyAValError("Name '" + str(name) + "' already exists!", \
                                 where="addPyAPS::Param"))
        self.pmap[name] = PyAPa(value)
    
    def _checkParam(self, n):
        """ Throws and exception if parameter 'n' does not exist """
        if not n in self.pmap:
            raise(PE.PyAValError("No such parameter: " + str(n),
                                 solution="Use one of: " + ', '.join(list(self.pmap))))
    
    def __getitem__(self, n, ref=False):
        """ Get reference to PyaPa """
        self._checkParam(n)
        if ref:
            return self.pmap[n]
        else:
            return self.pmap[n].value
    
    def __setitem__(self, n, v):
        """ Set value and update related if necessary """
        self._checkParam(n)
        self.pmap[n].value = v
        if len(self.pmap[n].affects) > 0:
            for a in self.pmap[n].affects:
                a.updateRelation()

    def copy(self):
        """ Return a shallow copy of the object """
        c = PyABPS([], self.rootName, number=self.number)
        c.pmap = copy.copy(self.pmap)
        return c

    def __init__(self, pns, rootName, number=0):
        self.rootName = rootName
        self.number = number
        self.pmap = bidict.OrderedBidict()
        for n in pns:
            self.addParam(n, 0.0)
        
      
      
class PyABaSoS(object):
    """
    Basic Set of Basic Parameter Sets
    """
    
    def _allRoots(self):
        """ Get root names of all basic sets """
        return [s.rootName for s in self.bss]
    
    def _rootNumbers(self, rn):
        """ All number associated with a certain root """
        return set([b.number if b.rootName == rn else 0 for b in self.bss])
    
    def _updatepmap(self):
        """ Update parameter names according to root name and number. """
        ar = self._allRoots()
        if len(ar) == 1:
            # Only one subset. Apply no name updating
            self.pmap = bidict.OrderedBidict()
            for n in list(self.bss[0].pmap):
                self.pmap[n] = self.bss[0].pmap[n]
        else:
            # More than one subset
            # Root Count
            rc = {r:ar.count(r) for r in set(ar)}
            self.pmap = bidict.OrderedBidict()
            for b in self.bss:
                useNumber = int(rc[b.rootName] > 1)
                for n in list(b.pmap):
                    useName = self._composePN(n, b.rootName, b.number*useNumber)
                    self.pmap[useName] = b.pmap[n]
    
    def addBPS(self, s):
        """
        Add Basic Parameter Set to set of sets
        
        Parameters
        ----------
        s : PyABPS
            Instance of PyABPS
        """
        snew = s.copy()
        rns = self._rootNumbers(snew.rootName)
        if len(rns) == 0:
            snew.number = 1
        else:
            snew.number = max(rns)+1
        self.bss.append(snew)
        self._updatepmap()
    
    def _checkParam(self, n):
        """ Throws and exception if parameter 'n' does not exist """
        if not n in self.pmap:
            raise(PE.PyAValError("No such parameter: " + str(n),
                                 solution="Use one of: " + ', '.join(list(self.pmap))))
    
    def __getitem__(self, n):
        """ Get value of parameter """
        self._checkParam(n)
        return self.pmap[n].value
    
    def getPRef(self, n):
        """ Get reference to PyaPa """
        self._checkParam(n)
        return self.pmap[n]
    
    def __setitem__(self, n, v):
        """ Set value and update related if necessary """
        self._checkParam(n)
        self.pmap[n].value = v
        if len(self.pmap[n].affects) > 0:
            for a in self.pmap[n].affects:
                a.updateRelation()

    def rename(self, old, new):
        """
        Rename parameter
        
        Parameters
        ----------
        old, new : strings
            Old and new name
        """
        self._checkParam(old)
        if new in self.pmap:
            raise(PE.PyAValError("Parameter " + str(new) + " already exists.", \
                                 solution="Use a unique name."))
        tmp = self.pmap[old]
        del self.pmap[old]
        self.pmap[new] = tmp
        
    def relate(self, dv, idv, func=None):
        if isinstance(idv, six.string_types):
            # Convert single string into list
            idv = [idv]
        # Check all parameters
        self._checkParam(dv)
        [self._checkParam(n) for n in idv]
        
        # By default, use equal
        if func is None:
            func = lambda x:x
        
        # Manage dependent variable
        self.pmap[dv].dependsOn = [self.pmap[n] for n in idv]
        self.pmap[dv].relation = PyARelation(self.pmap[dv].dependsOn, func)
        # Manage affected variables
        for n in idv:
            self.pmap[n].affects.update([self.pmap[dv]])
        # Trigger update of the value of the related parameter
        self.pmap[dv].updateRelation()
    
    def copy(self):
        """ Return a shallow copy of the object """
        c = PyABaSoS()
        c.pmap = copy.copy(self.pmap)
        c.bss = copy.copy(self.bss)
        return c
    
    @classmethod
    def combine(cls, left, right):
        """
        Combine parameter sets
        
        Parameters
        ----------
        left, right : PyABaSoS
            Instances of PyABaSoS holding the individual sets
        
        Returns
        -------
        Combined object : PyABaSoS
        """
        c = cls()
        c.bss = []
        for b in left.bss:
            c.addBPS(b)
        for b in right.bss:
            c.addBPS(b)
        return c
    
    def _composePN(self, b, r, n):
        """
        Compose parameter name
        
        Parameters
        ----------
        b, r : strings
            Base and root
        n : int
            Number
        
        Returns
        -------
        Name : string
            Complete parameter name
        """
        result = b
        if not r is None:
            result += "_" + r
        if (not n is None) and (n != 0):
            result += ("(%d)" % n)
        return result
    
    def _decomposePN(self, n):
        """
        Decompose parameter name into Base, Root, Number
        """
        r = re.match("([^_]+)((_([^\(]+)?)?(\(([0-9]+)\))?)?", n)
        if r is None:
            return None, None, None
        else:
            return r.group(0), r.group(3), r.group(5)
   
    def parameters(self):
        """
        Get all parameter names and values
        
        Returns
        -------
        Parameters : OrderedDict (collections)
            (Ordered) Dictionary mapping parameter name to value
        """
        ps = collections.OrderedDict()
        for k, v in six.iteritems(self.pmap):
            ps[k] = v.value
        return ps
    
    def freeParamNames(self):
        """ Get list of names of free parameters """
        return [p for p in list(self.pmap) if self.pmap[p].free]
    
    def freeParamVals(self):
        """ Get list of names of free parameters """
        return [self.pmap[p].value for p in list(self.pmap) if self.pmap[p].free]
    
    def setFreeParamVals(self, vals):
        """
        Assign values to free parameters.
        
        The order is fixed (use, e.g., freeParamNames to get it).
        
        Parameters
        ----------
        vals : list or array
            The values to be assigned
        """
        for i, p in enumerate(self.freeParamNames()):
            self[p] = vals[i]
    
    def _thawfreeze(self, pns, free):
        """
        Thaw or freeze parameters
        
        Parameters
        ----------
        pns : string or list of strings
            Relevant parameters
        free : boolean
            True to thaw and False to freeze
        """
        if isinstance(pns, six.string_types):
            pns = [pns]
        for p in pns:
            self._checkParam(p)
            self.pmap[p].free = True
    
    def thaw(self, pns):
        """
        Thaw parameters
        
        Parameters
        ----------
        pns : string or list of strings
            Relevant parameters
        """
        self._thawfreeze(pns, True)
    
    def freeze(self, pns):
        """
        Freeze parameters
        
        Parameters
        ----------
        pns : string or list of strings
            Relevant parameters
        """
        self._thawfreeze(pns, False)
    
    def __init__(self, *args):
        self.bss = []
        for s in args:
            self.addBPS(s)
        self._updatepmap()
      

class PStat(object):
    
    def setStatMode(self, mode):
        if mode == "default":
            self.logL = self._deflogL
        elif (mode is None) or (mode == "manual"):
            pass
        else:
            raise(PE.PyAValError("Unknown mode for PyABay: " + str(mode)))
    
    def __init__(self, statmode="default"):
        self.priors = []
        self.setStatMode(statmode)
        self.statmode = statmode
        
    def logL(self, *args, **kwargs):
        raise(PE.PyANotImplemented("The method 'logL' needs to be implemented."))

    def logPrior(self, *args, **kwargs):
        result = 0.0
        for p in self.priors:
            result += p.evaluate()
            if np.isinf(result):
                return result
        return result
    
    def addUniformPrior(self, pn, lower=None, upper=None):
        """
        Add a uniform prior
        
        Parameters
        ----------
        pn : string
            Name of the parameter
        lower, upper : float, optional
            Lower and upper bounds
        """
        self.pars._checkParam(pn)
        descr = "Uniform prior on '" + str(pn) + "' (lower = %g, upper = %g)" % (lower or -np.inf, upper or np.inf)
        self.priors.append(PyAUniformPrior(self.getPRef(pn), lower=lower, upper=upper, descr=descr))
      
    def addSmoothUniformPrior(self, pn, lower=None, upper=None, scale=1e-9):
        """
        Add a uniform prior with 'smoothed' (arctan) edges
        
        Parameters
        ----------
        pn : string
            Name of the parameter
        lower, upper : float, optional
            Lower and upper bounds
        """
        self.pars._checkParam(pn)
        descr = "Smooth uniform prior on '" + str(pn) + "' (lower = %g, upper = %g, scale = %g)" % \
                (lower or -np.inf, upper or np.inf, scale)
        self.priors.append(PyASmoothUniformPrior(self.getPRef(pn), lower=lower, upper=upper, descr=descr))
        
    def addPrior(self, p):
        pass
    
    def margD(self, *args, **kwargs):
        return None

    def _deflogL(self, *args, **kwargs):
        
        if len(args) == 2:
            x, y = args[0], args[1]
            yerr = 1.0
        elif len(args) == 3:
            x, y, yerr = args[0], args[1], args[2]
        else:
            raise(PE.PyAValError("Invalid call to logL of PyABayDef. No. of arguments: " + str(len(args))))

        if "_currentModel" in kwargs:
            m = kwargs["_currentModel"]
        else:
            m = self.evaluate(x)
        
        return -np.sum((m-y)**2/yerr**2)

    def logPost(self, *args, **kwargs):
        """ Get log of the posterior """
        lp = self.logPrior(*args, **kwargs) + self.logL(*args, **kwargs)
        md = self.margD()
        if not md is None:
            lp -= md
        return lp

    def objf(self, *args, **kwargs):
        self.setFreeParamVals(args[0])
        return -self.logPost(*args[1:], **kwargs)
  
        

class MBO2(PStat):
    """
    Model Base Object
    
    Concepts
    --------
    
    Parameter names: Internal vs. external
        
    
    
    """
    
    def __init__(self, pars, rootName="", statmode="default"):
        PStat.__init__(self, statmode=statmode)
        self.pars = (PyABaSoS(PyABPS(pars, rootName)))
        self._imap = self.pars.copy()
        
        self.rootName = rootName
        self.leftCompo = None
        self.rightCompo = None
     
    def _combineMBOs(self, right):
        """
        Combine two MBO2s into a new one

        Parameters
        ----------
        right : MBO2 instance
            Right side of the operation
        """
        r = MBO2([], rootName="combined")
        r.pars = PyABaSoS.combine(self.pars, right.pars)
        r.leftCompo = self
        r.rightCompo = right
        return r
     
    def __add__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) + self.rightCompo.evaluate(*args, **kwargs), result)
        return result
    
    def __sub__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) - self.rightCompo.evaluate(*args, **kwargs), result)
        return result
    
    def __mul__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) * self.rightCompo.evaluate(*args, **kwargs), result)
        return result
    
    def __div__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) / self.rightCompo.evaluate(*args, **kwargs), result)
        return result
    
    def __truediv__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) / self.rightCompo.evaluate(*args, **kwargs), result)
        return result

    def __pow__(self, right):
        result = self._combineMBOs(right)
        result.evaluate = types.MethodType(lambda self, *args, **kwargs: \
                                           self.leftCompo.evaluate(*args, **kwargs) ** self.rightCompo.evaluate(*args, **kwargs), result)
        return result

    def parameterSummary(self):
        for k, v in self.pars.pmap.items():
            print(k, v.value)
        
    def evaluate(self, *args, **kwargs):
        pass
    
    def relate(self, dv, idv, func=None):
        self.pars.relate(dv, idv, func)
    
    def __getitem__(self, n):
        return self.pars[n]
    
    def __setitem__(self, n, v):
        self.pars[n] = v
    
    def getPRef(self, n):
        return self.pars.getPRef(n)
    
    def freeParamVals(self):
        return self.pars.freeParamVals()
    
    def freeParamNames(self):
        return self.pars.freeParamNames()
    
    def setFreeParamVals(self, vals):
        self.pars.setFreeParamVals(vals)
        
    def thaw(self, pns):
        self.pars.thaw(pns)
        
    def freeze(self, pns):
        self.pars.freeze(pns)
    
    def setRestriction(self, restricts, scale=1e-9):
        for k, v in six.iteritems(restricts):
            self.pars._checkParam(k)
            self.addSmoothUniformPrior(k, lower=v[0], upper=v[1], scale=scale)


def fitfmin1d(m, x, y, yerr=None, **kwargs):
    """
    Use scipy's fmin to fit 1d model.
    """
    # Get keywords and default arguments
    fi = inspect.getargspec(sco.fmin)
    defargs = dict(zip(fi.args[-len(fi.defaults):],fi.defaults))
    
    for k in list(defargs):
        if k in kwargs:
            defargs[k] = kwargs[k]
    
    if not yerr is None:
        defargs["args"] = (x, y, yerr)
    else:
        defargs["args"] = (x, y)
    
    defargs["full_output"] = True
    
    fr = sco.fmin(m.objf, m.freeParamVals(), **defargs)
    m.setFreeParamVals(fr[0])
    return fr


class Poly2(MBO2):
    
    def __init__(self):
        MBO2.__init__(self, ["c0", "c1", "c2"], rootName="Poly2")
    
    def evaluate(self, x, **kwargs):
        s = self._imap
        return s["c0"] + s["c1"]*x + s["c2"]*x**2
    