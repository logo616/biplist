from collections import namedtuple
from cStringIO import StringIO
import datetime
import math
import plistlib
from struct import pack, unpack
import time

__all__ = [
    'Uid', 'Data', 'readPlist', 'writePlist', 'readPlistFromString',
    'writePlistToString', 'InvalidPlistException', 'NotBinaryPlistException'
]

apple_reference_date_offset = 978307200

class Uid(int):
    """Wrapper around integers for representing UID values."""
    pass

class Data(str):
    """Wrapper around str types for representing Data values."""
    pass

class InvalidPlistException(Exception):
    """Raised when the plist is incorrectly formatted."""
    pass

class NotBinaryPlistException(Exception):
    """Raised when a binary plist was expected but not encountered."""
    pass

def readPlist(pathOrFile):
    """Raises NotBinaryPlistException, InvalidPlistException"""
    didOpen = False
    result = None
    if isinstance(pathOrFile, (str, unicode)):
        pathOrFile = open(pathOrFile)
        didOpen = True
    try:
        reader = PlistReader(pathOrFile)
        result = reader.parse()
    except NotBinaryPlistException, e:
        try:
            result = plistlib.readPlist(pathOrFile)
        except Exception, e:
            raise InvalidPlistException(e)
    if didOpen:
        pathOrFile.close()
    return result

def writePlist(rootObject, pathOrFile, binary=True):
    if not binary:
        return plistlib.writePlist(rootObject, pathOrFile)
    else:
        didOpen = False
        if isinstance(pathOrFile, (str, unicode)):
            pathOrFile = open(pathOrFile, 'w')
            didOpen = True
        writer = PlistWriter(pathOrFile)
        result = writer.writeRoot(rootObject)
        if didOpen:
            pathOrFile.close()
        return result

def readPlistFromString(data):
    return readPlist(StringIO(data))

def writePlistToString(rootObject, binary=True):
    if not binary:
        return plistlib.writePlistToString(rootObject)
    else:
        io = StringIO()
        writer = PlistWriter(io)
        writer.writeRoot(rootObject)
        return io.getvalue()

def is_stream_binary_plist(stream):
    stream.seek(0)
    header = stream.read(7)
    if header == 'bplist0':
        return True
    else:
        return False

PlistTrailer = namedtuple('PlistTrailer', 'offsetSize, objectRefSize, offsetCount, topLevelObjectNumber, offsetTableOffset')
PlistByteCounts = namedtuple('PlistByteCounts', 'boolBytes, intBytes, realBytes, dateBytes, dataBytes, stringBytes, uidBytes, arrayBytes, setBytes, dictBytes')

class PlistReader(object):
    file = None
    contents = ''
    offsets = None
    root = None
    trailer = None
    uniques = None
    currentOffset = 0
    
    def __init__(self, fileOrStream):
        """Raises NotBinaryPlistException."""
        self.reset()
        self.file = fileOrStream
    
    def parse(self):
        return self.readRoot()
    
    def reset(self):
        self.trailer = None
        self.contents = ''
        self.offsets = []
        self.uniques = []
        self.root = None
        self.currentOffset = 0
    
    def readRoot(self):
        self.reset()
        # Get the header, make sure it's a valid file.
        if not is_stream_binary_plist(self.file):
            raise NotBinaryPlistException()
        self.file.seek(0)
        self.contents = self.file.read()
        if len(self.contents) < 32:
            raise InvalidPlistException("File is too short.")
        trailerContents = self.contents[-32:]
        try:
            self.trailer = PlistTrailer._make(unpack("!xxxxxxBBQQQ", trailerContents))
            print "trailer:", self.trailer
            offset_size = self.trailer.offsetSize * self.trailer.offsetCount
            offset = self.trailer.offsetTableOffset
            offset_contents = self.contents[offset:offset+offset_size]
            offset_i = 0
            while offset_i < self.trailer.offsetCount:
                begin = self.trailer.offsetSize*offset_i
                tmp_contents = offset_contents[begin:begin+self.trailer.offsetSize]
                tmp_sized = self.getSizedInteger(tmp_contents, self.trailer.offsetSize)
                self.offsets.append(tmp_sized)
                offset_i += 1
            self.setCurrentOffsetToObjectNumber(self.trailer.topLevelObjectNumber)
            self.root = self.readObject()
            print self.offsets
        except TypeError, e:
            raise InvalidPlistException(e)
        print "root is:", self.root
        return self.root
    
    def setCurrentOffsetToObjectNumber(self, objectNumber):
        self.currentOffset = self.offsets[objectNumber]
    
    def readObject(self):
        result = None
        tmp_byte = self.contents[self.currentOffset:self.currentOffset+1]
        marker_byte = unpack("!B", tmp_byte)[0]
        format = (marker_byte >> 4) & 0x0f
        extra = marker_byte & 0x0f
        self.currentOffset += 1
        
        def proc_extra(extra):
            if extra == 0b1111:
                self.currentOffset += 1
                extra = self.readObject()
            return extra
        
        # bool or fill byte
        if format == 0b0000:
            if extra == 0b1000:
                result = False
            elif extra == 0b1001:
                result = True
            elif extra == 0b1111:
                pass # fill byte
            else:
                raise InvalidPlistException("Invalid object found.")
        # int
        elif format == 0b0001:
            extra = proc_extra(extra)
            result = self.readInteger(pow(2, extra))
        # real
        elif format == 0b0010:
            extra = proc_extra(extra)
            result = self.readReal(extra)
        # date
        elif format == 0b0011 and extra == 0b0011:
            result = self.readDate()
        # data
        elif format == 0b0100:
            extra = proc_extra(extra)
            result = self.readData(extra)
        # ascii string
        elif format == 0b0101:
            extra = proc_extra(extra)
            result = self.readAsciiString(extra)
        # Unicode string
        elif format == 0b0110:
            extra = proc_extra(extra)
            result = self.readUnicode(extra)
        # uid
        elif format == 0b1000:
            result = self.readUid(extra)
        # array
        elif format == 0b1010:
            extra = proc_extra(extra)
            result = self.readArray(extra)
        # set
        elif format == 0b1100:
            extra = proc_extra(extra)
            result = set(self.readArray(extra))
        # dict
        elif format == 0b1101:
            extra = proc_extra(extra)
            result = self.readDict(extra)
        else:    
            raise InvalidPlistException("Invalid object found: {format: %s, extra: %s}" % (bin(format), bin(extra)))
        return result
    
    def readInteger(self, bytes):
        result = 0
        original_offset = self.currentOffset
        data = self.contents[self.currentOffset:self.currentOffset+bytes]
        # 1, 2, and 4 byte integers are unsigned
        if bytes == 1:
            result = unpack('>B', data)[0]
        elif bytes == 2:
            result = unpack('>H', data)[0]
        elif bytes == 4:
            result = unpack('>L', data)[0]
        elif bytes == 8:
            result = unpack('>q', data)[0]
        else:
            #!! This doesn't work?
            i = 0
            while i < bytes:
                self.currentOffset += 1
                result += (result << 8) + unpack('>B', self.contents[i])[0]
                i += 1
        self.currentOffset = original_offset + bytes
        return result
    
    def readReal(self, length):
        result = 0.0
        to_read = pow(2, length)
        data = self.contents[self.currentOffset:self.currentOffset+to_read]
        if length == 2: # 4 bytes
            result = unpack('>f', data)[0]
        elif length == 3: # 8 bytes
            result = unpack('>d', data)[0]
        else:
            raise InvalidPlistException("Unknown real of length %d bytes" % to_read)
        return result
    
    def readRefs(self, count):    
        refs = []
        i = 0
        while i < count:
            fragment = self.contents[self.currentOffset:self.currentOffset+self.trailer.objectRefSize]
            ref = self.getSizedInteger(fragment, len(fragment))
            refs.append(ref)
            self.currentOffset += self.trailer.objectRefSize
            i += 1
        return refs
    
    def readArray(self, count):
        result = []
        values = self.readRefs(count)
        i = 0
        while i < len(values):
            self.setCurrentOffsetToObjectNumber(values[i])
            value = self.readObject()
            result.append(value)
            i += 1
        return result
    
    def readDict(self, count):
        result = {}
        keys = self.readRefs(count)
        values = self.readRefs(count)
        i = 0
        while i < len(keys):
            self.setCurrentOffsetToObjectNumber(keys[i])
            key = self.readObject()
            self.setCurrentOffsetToObjectNumber(values[i])
            value = self.readObject()
            result[key] = value
            i += 1
        return result
    
    def readAsciiString(self, length):
        result = unpack("!%ds" % length, self.contents[self.currentOffset:self.currentOffset+length])[0]
        self.currentOffset += length
        return result
    
    def readUnicode(self, length):
        data = self.contents[self.currentOffset:self.currentOffset+length*2]
        data = unpack(">%ds" % (length*2), data)[0]
        self.currentOffset += length * 2
        return data.decode('utf-16-be')
    
    def readDate(self):
        global apple_reference_date_offset
        result = unpack(">d", self.contents[self.currentOffset:self.currentOffset+8])[0]
        result = datetime.datetime.utcfromtimestamp(result + apple_reference_date_offset)
        self.currentOffset += 8
        return result
    
    def readData(self, length):
        result = self.contents[self.currentOffset:self.currentOffset+length]
        self.currentOffset += length
        return Data(result)
    
    def readUid(self, length):
        return Uid(self.readInteger(length+1))
    
    def getSizedInteger(self, data, intSize):
        result = 0
        i = 0
        d_read = ''
        while i < intSize:
            d_read += bin(unpack('!B', data[i])[0])
            result += (result << 8) + unpack('!B', data[i])[0]
            i += 1
        return result

class HashableWrapper(object):
    def __init__(self, value):
        self.value = value

class PlistWriter(object):
    file = None
    byteCounts = None
    offsets = None
    serializedUniques = None
    trailer = None
    uniques = None
    uniquePositions = None
    references = 0
    header = 'bplist00bybiplist1.0'
    trailer_size = 32
    
    def __init__(self, file):
        self.reset()
        self.file = file

    def reset(self):
        self.byteCounts = PlistByteCounts(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.offsets = []
        self.serializedUniques = ''
        self.trailer = PlistTrailer(0, 0, 0, 0, 0)
        self.uniques = []
        self.references = 0
        
        # A set of all the uniques which have been computed.
        self.computedUniques = set()
        # A count of all the references.
        self.computedReferenceCount = 0
        # A list of all the uniques which have been written.
        self.writtenReferences = []
        # A dict of the positions of the written uniques.
        self.referencePositions = {}
        
    def positionOfObjectReference(self, obj):
        """If the given object has been written already, return its
           position in the offset table. Otherwise, return None."""
        if obj in self.writtenReferences:
            return self.writtenReferences.index(obj)
        return None
        
    def writeRoot(self, root):
        """
        Strategy is:
        - write header
        - wrap root object so everything is hashable
        - compute size of objects which will be written
          - need to do this in order to know how large the object refs
            will be in the list/dict/set reference lists
        - write objects
          - keep objects in writtenReferences
          - keep positions of object references in referencePositions
          - write object references with the length computed previously
        - computer object reference length
        - write object reference positions
        - write trailer
        """
        output = self.header
        wrapped_root = self.wrapRoot(root)
        self.computeOffsets(wrapped_root, asReference=True)
        self.trailer = self.trailer._replace(**{'objectRefSize':self.intSize(self.computedReferenceCount)})
        (_, output) = self.writeObjectReference(wrapped_root, output)
        output = self.writeObject(wrapped_root, output, setReferencePosition=True)
        
        # output size at this point is an upper bound on how big the
        # object reference offsets need to be.
        self.trailer = self.trailer._replace(**{
            'offsetSize':self.intSize(len(output)),
            'offsetCount':self.computedReferenceCount,
            'offsetTableOffset':len(output),
            'topLevelObjectNumber':0
            })
        
        output = self.writeOffsetTable(output)
        output += pack('!xxxxxxBBQQQ', *self.trailer)
        
        print "trailer write:", self.trailer
        
        self.file.write(output)
    
    def wrapRoot(self, root):
        if isinstance(root, set):
            n = set()
            for value in root:
                n.add(self.wrapRoot(value))
            return HashableWrapper(n)
        elif isinstance(root, dict):
            n = {}
            for key, value in root.iteritems():
                n[self.wrapRoot(key)] = self.wrapRoot(value)
            return HashableWrapper(n)
        elif isinstance(root, list):
            n = []
            for value in root:
                n.append(self.wrapRoot(value))
            return HashableWrapper(n)
        elif isinstance(root, tuple):
            n = tuple(*[self.wrapRoot(value) for value in root])
            return HashableWrapper(n)
        else:
            return root

    def incrementByteCount(self, field, incr=1):
        self.byteCounts = self.byteCounts._replace(**{field:self.byteCounts.__getattribute__(field) + incr})

    def computeOffsets(self, obj, asReference=False):
        def proc_size(size):
            if size > 0b1110:
                size += self.intSize(size)
            return size
        # If this should be a reference, then we keep a record of it in the
        # uniques table.
        if asReference:
            # Work around Python detecting if two sets, etc are the same...
            if isinstance(obj, (HashableWrapper, set)):
                self.computedReferenceCount += 1
            else:
                self.computedReferenceCount += 1
                if obj in self.computedUniques:
                    return
                else:
                    self.computedUniques.add(obj)
        
        if type(obj) == bool:
            self.incrementByteCount('boolBytes')
        elif isinstance(obj, Uid):
            size = self.intSize(obj)
            self.incrementByteCount('uidBytes', incr=1+size)
        elif isinstance(obj, (int, long)):
            size = self.intSize(obj)
            self.incrementByteCount('intBytes', incr=1+size)
        elif isinstance(obj, (float)):
            size = self.floatSize(obj)
            self.incrementByteCount('realBytes', incr=1+size)
        elif isinstance(obj, datetime.datetime):    
            self.incrementByteCount('dateBytes', incr=2)
        elif isinstance(obj, Data):
            size = proc_size(len(obj))
            self.incrementByteCount('dataBytes', incr=1+size)
        elif isinstance(obj, (str, unicode)):
            size = proc_size(len(obj))
            self.incrementByteCount('stringBytes', incr=1+size)
        elif isinstance(obj, HashableWrapper):
            obj = obj.value
            if isinstance(obj, set):
                size = proc_size(len(obj))
                self.incrementByteCount('setBytes', incr=1+size)
                for value in obj:
                    self.computeOffsets(value, asReference=True)
            elif isinstance(obj, (list, tuple)):
                size = proc_size(len(obj))
                self.incrementByteCount('arrayBytes', incr=1+size)
                for value in obj:
                    self.computeOffsets(value, asReference=True)
            elif isinstance(obj, dict):
                size = proc_size(len(obj))
                self.incrementByteCount('dictBytes', incr=1+size)
                for key, value in obj.iteritems():
                    self.computeOffsets(key, asReference=True)
                    self.computeOffsets(value, asReference=True)
        else:
            raise InvalidPlistException("Unknown object type.")

    def writeObjectReference(self, obj, output):
        """Tries to write an object reference, adding it to the references
           table. Does not write the actual object bytes or set the reference
           position. Returns a tuple of whether the object was a new reference
           (True if it was, False if it already was in the reference table)
           and the new output.
        """
        position = self.positionOfObjectReference(obj)
        if position is None:
            self.writtenReferences.append(obj)
            output += self.binaryInt(len(self.writtenReferences) - 1, bytes=self.trailer.objectRefSize)
            return (True, output)
        else:
            output += self.binaryInt(position, bytes=self.trailer.objectRefSize)
            return (False, output)

    def writeObject(self, obj, output, setReferencePosition=False):
        """Serializes the given object to the output. Returns output.
           If setReferencePosition is True, will set the position the
           object was written.
        """
        def proc_variable_length(format, length):
            result = ''
            if length > 0b1110:
               result += pack('!B', (format << 4) | 0b1111)
               result += self.binaryInt(length)
            else:
               result += pack('!B', (format << 4) | length)
            return result
        
        if setReferencePosition:
            self.referencePositions[obj] = len(output)
        
        if type(obj) == bool:
            if obj is False:
                output += pack('!B', 0b00001000)
            else:
                output += pack('!B', 0b00001001)
        elif isinstance(obj, Uid):
            size = self.intSize(obj)
            output += pack('!B', (0b1000 << 4) | size - 1)
            output += self.binaryInt(Uid)
        elif isinstance(obj, (int, long)):
            bytes = self.intSize(obj)
            root = math.log(bytes, 2)
            output += pack('!B', (0b0001 << 4) | int(root))
            output += self.binaryInt(obj)
        elif isinstance(obj, float):
            # just use doubles
            output += pack('!B', (0b0010 << 4) | 3)
            output += self.binaryReal(obj)
        elif isinstance(obj, datetime.datetime):
            timestamp = time.mktime(obj.timetuple())
            timestamp -= apple_reference_date_offset
            output += pack('!B', 0b00110011)
            output += pack('!d', float(timestamp))
        elif isinstance(obj, Data):
            output += proc_variable_length(0b0100, len(obj))
            output += obj
        elif isinstance(obj, (str, unicode)):
            if isinstance(obj, unicode):
                bytes = obj.encode('utf-16be')
                output += proc_variable_length(0b0110, len(bytes))
            else:
                bytes = obj
                output += proc_variable_length(0b0101, len(bytes))
                output += bytes
        elif isinstance(obj, HashableWrapper):
            obj = obj.value
            if isinstance(obj, (set, list, tuple)):
                if isinstance(obj, set):
                    output += proc_variable_length(0b1100, len(obj))
                else:
                    output += proc_variable_length(0b1010, len(obj))
            
                objectsToWrite = []
                for objRef in obj:
                    (isNew, output) = self.writeObjectReference(objRef, output)
                    if isNew:
                        objectsToWrite.append(objRef)
                for objRef in obj:
                    output = self.writeObject(objRef, output, setReferencePosition=True)
            elif isinstance(obj, dict):
                output += proc_variable_length(0b1101, len(obj))
                keys = []
                values = []
                objectsToWrite = []
                for key, value in obj.iteritems():
                    keys.append(key)
                    values.append(value)
                for key in keys:
                    (isNew, output) = self.writeObjectReference(key, output)
                    if isNew:
                        objectsToWrite.append(key)
                for value in values:
                    (isNew, output) = self.writeObjectReference(value, output)
                    if isNew:
                        objectsToWrite.append(value)
                for objRef in objectsToWrite:
                    output = self.writeObject(objRef, output, setReferencePosition=True)
        return output
    
    def writeOffsetTable(self, output):
        """Writes all of the object reference offsets."""
        for obj in self.writtenReferences:
            position = self.referencePositions.get(obj, None)
            if position == None:
                raise InvalidPlistException("Error while writing offsets table. Object not found.")
            output += self.binaryInt(position, self.trailer.offsetSize)
        return output
    
    def binaryReal(self, obj):
        # just use doubles
        result = pack('>d', obj)
        return result
    
    def binaryInt(self, obj, bytes=None):
        result = ''
        if bytes is None:
            #!! compute actual size
            bytes = self.intSize(obj)
        
        if bytes == 1:
            result += pack('>B', obj)
        elif bytes == 2:
            result += pack('>H', obj)
        elif bytes == 4:
            result += pack('>L', obj)
        elif bytes == 8:
            result += pack('>q', obj)
        else:
            #!! Uh... what to do here?
            raise NotImplementedError("Not sure how to do this yet.")
        return result
    
    def intSize(self, obj):
        #!! Should actually calculate size required.
        return 8
