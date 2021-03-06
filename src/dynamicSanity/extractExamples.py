# -*- coding: utf-8 -*-
import os
import re
import argparse
import ConfigParser
import hashlib
import datetime


class CandidateBlock:
  
  minLength = 10
  
  def __init__(self):
    self.lines = []
    # The starting line (in the file) of the example
    self.startLine = None
    # True if the block is long enough to be an example
    self.lengthCheck = False
    # True if import statement has been found
    self.hasImport = False
    # Level if indention
    self.indentLevel = 1e100
    # Is this thought to be an example
    self.isExample = False
    # Is manually specified as example (by ".. IsPyAExample" directive)
    self.hasExampleDirective = False

  def characterize(self):
    """
      Check whether this block is a potential example.
    """
    # Check block length (count only nonempty lines)
    no = 0
    for l in self.lines:
      r = re.match("(\s*)[^\s]+.*", l) 
      if r is not None:
        no += 1
        if len(r.group(1)) < self.indentLevel:
          self.indentLevel = len(r.group(1))
      if re.match("\s*import\s.*", l) is not None:
        self.hasImport = True
    if no > self.minLength:
      self.lengthCheck = True
    
    if (self.lengthCheck and self.hasImport) or self.hasExampleDirective:
      self.isExample = True
  
  def commentShow(self):
    for i in xrange(len(self.lines)):
      r = re.match("(\s*)([^#]*\.show().*)", self.lines[i])
      if r is not None:
        self.lines[i] = r.group(1) + "# " + r.group(2)
        return

  def format(self, indentLevel=2, commentShow=True):
    if commentShow:
      self.commentShow()
    result = []
    indentor = "".join([" " for i in range(indentLevel)])
    result.append(indentor + "# Automatically extracted example:\n")
    result.append(indentor + "# Starting line: " + str(self.startLine) + "\n")
    for l in self.lines:
      r = re.match("\s{" + str(self.indentLevel) + "}(.*)", l)
      if r is not None:
        result.append(indentor + r.group(1) + "\n")
    return result
    
  def executeBlock(self, fn="tmp.py"):
    self.commentShow()
    f = open(fn, 'w')
    for l in self.lines:
      r = re.match("\s{" + str(self.indentLevel) + "}(.*)", l)
      if r is not None:
        f.write(r.group(1) + "\n")
    f.close()
    try:
      exec(open(fn), {})
    except:
      pass
    
    
    

class FileBlocks:
  
  def _findBlocks(self, lines):
    """
      Find potential example blocks and save them into self.blocks
      as CandidateBlock objects.
    """
    isInBlock = False
    indent = 0
    lineNo = 0
    lastLine_IsPyaExample = False
    
    for l in lines:
      lineNo += 1
      r = re.match(".. IsPyAExample", l)
      if r is not None:
        lastLine_IsPyaExample = True
        continue
      r = re.match("(\s*)::\s*", l)
      if r is not None:
        isInBlock = True
        block = CandidateBlock()
        # Classifies as example if specified manually
        block.hasExampleDirective = lastLine_IsPyaExample
        block.startLine = lineNo
        indent = len(r.group(1))
        lastLine_IsPyaExample = False
        continue
      r = re.match("(\s*)([^\s]+)", l)
      if r is not None:
        # Not empty line
        lastLine_IsPyaExample = False
      if not isInBlock:
        continue
      if r is None:
        # Empty line
        block.lines.append(l)
        continue
      if len(r.group(1)) == indent:
        isInBlock = False
        self.blocks.append(block)
        continue
      block.lines.append(l)
    if isInBlock:
      # File seems to end with a potential example block
      self.blocks.append(block)
  
  def _characterizeBlocks(self):
    for i in xrange(len(self.blocks)):
      self.blocks[i].characterize()
  
  def noOfExamples(self):
    i = 0
    for b in self.blocks:
      if b.isExample: i += 1
    return i 
  
  def summary(self):
    """
      Print a summary of the analysis
    """
    print "Analyzed file: ", self.fn
    print "Found " + str(len(self.blocks)) + " potential example blocks"
    i = self.noOfExamples()
    print "  of which " + str(i) + " have been characterized as example."
    print
  
  def executeExamples(self):
    for i in xrange(len(self.blocks)):
      if self.blocks[i].isExample:
        self.blocks[i].executeBlock()
 
  def __checkCreateTestDir(self):
    if os.path.isdir(self.snDir):
      return
    os.mkdir(self.snDir)
  
  def toSanityCheckFile(self):
    """
      Write all blocks identified as examples to a file identified by
      the sha1 hash of the full file name.
    """
    if self.noOfExamples() == 0: return
    s1 = hashlib.sha1()
    s1.update(self.fn)
    ofn = os.path.join(self.snDir, "sanity_" + s1.hexdigest() + ".py")
    f = open(ofn, 'w')
    f.write("# File written by TBD at " + str(datetime.datetime.now()) + "\n")
    f.write("# Examples extracted from file: " + self.fn + "\n\n\n")
    for i in xrange(len(self.blocks)):
      if self.blocks[i].isExample:
        f.write("def sanity_line_" + str(self.blocks[i].startLine) + "():\n")
        lines = self.blocks[i].format()
        f.writelines(lines)
        f.write("\n\n")
    f.close()
  
  def __init__(self, fn, snDir="sanityies"):
    
    if not os.path.isfile(fn):
      print "No such file: ", fn
      return
    
    self.fn = fn
    self.blocks = []
    self.snDir = snDir
    self.__checkCreateTestDir()
    
    lines = open(fn).readlines()
    self._findBlocks(lines)
    self._characterizeBlocks()
    


class Walker:
  
  def __init__(self, rootPath="../", fnRegex=".*\.rst"):
    self.rootPath = rootPath
    self.fnRegex = fnRegex
  
  def walk(self):
    for root, dirs, files in os.walk(self.rootPath):
      for fn in files:
        if re.match(self.fnRegex, fn) is not None:
          yield os.path.join(root, fn)





parser = argparse.ArgumentParser(description='Options')
parser.add_argument('--cf', dest='cf', default="dysSetup.cfg",
                    help="Name of config file.")
parser.add_argument('--root', dest='root',
                    help="The root directory for the example search.")
args = parser.parse_args()


config = ConfigParser.ConfigParser()
config.read(args.cf)    

if args.root is None:
  rootPath = config.get("Walker", "rootPath")
else:
  rootPath = args.root

fnRegex = config.get("Walker", "fnRegex")
walker = Walker(rootPath=rootPath, fnRegex=fnRegex)

for fn in walker.walk():
  fbs = FileBlocks(fn)
  fbs.summary()
  fbs.toSanityCheckFile()
