''' Header reading functions for Analyze format '''

import numpy as np

from volumeutils import pretty_mapping, endian_codes, \
     native_code, swapped_code, hdr_getterfunc, \
     make_dt_codes, array_from_file, array_to_file, \
     HeaderDataError, HeaderTypeError, allopen, \
     can_cast

import volumeimages.imageglobals as imageglobals
import volumeimages.spatialimages as spatialimages
import volumeimages.filetuples as filetuples

from batteryrunners import BatteryRunner, Report

# Sub-parts of standard analyze header from 
# Mayo dbh.h file
header_key_dtd = [
    ('sizeof_hdr', 'i4'),
    ('data_type', 'S10'),
    ('db_name', 'S18'),
    ('extents', 'i4'),
    ('session_error', 'i2'),
    ('regular', 'S1'),
    ('hkey_un0', 'S1')
    ]
image_dimension_dtd = [
    ('dim', 'i2', 8),
    ('vox_units', 'S4'),
    ('cal_units', 'S8'),
    ('unused1', 'i2'),
    ('datatype', 'i2'),
    ('bitpix', 'i2'),
    ('dim_un0', 'i2'),
    ('pixdim', 'f4', 8),
    ('vox_offset', 'f4'),
    ('funused1', 'f4'),
    ('funused2', 'f4'),
    ('funused3', 'f4'),
    ('cal_max', 'f4'),
    ('cal_min', 'f4'),
    ('compressed', 'i4'),
    ('verified', 'i4'),
    ('glmax', 'i4'),
    ('glmin', 'i4')
    ]
data_history_dtd = [
    ('descrip', 'S80'),
    ('aux_file', 'S24'),
    ('orient', 'S1'),
    ('originator', 'S10'),
    ('generated', 'S10'),
    ('scannum', 'S10'),
    ('patient_id', 'S10'),
    ('exp_date', 'S10'),
    ('exp_time', 'S10'),
    ('hist_un0', 'S3'),
    ('views', 'i4'),
    ('vols_added', 'i4'),
    ('start_field', 'i4'),
    ('field_skip', 'i4'),
    ('omax', 'i4'),
    ('omin', 'i4'),
    ('smax', 'i4'),
    ('smin', 'i4')
    ]

# Full header numpy dtype combined across sub-fields
header_dtype = np.dtype(header_key_dtd + image_dimension_dtd + 
                        data_history_dtd)

_dtdefs = ( # code, conversion function, equivalent dtype, aliases
    (0, 'none', np.void), 
    (1, 'binary', np.void), # 1 bit per voxel, needs thought
    (2, 'uint8', np.uint8),
    (4, 'int16', np.int16),
    (8, 'int32', np.int32),
    (16, 'float32', np.float32),
    (32, 'complex64', np.complex64), # numpy complex format?
    (64, 'float64', np.float64),
    (128, 'RGB', np.dtype([('R','u1'),
                  ('G', 'u1'),
                  ('B', 'u1')])),
    (255, 'all', np.void))

# Make full code alias bank, including dtype column
data_type_codes = make_dt_codes(_dtdefs)


class AnalyzeHeader(object):
    ''' Class for basic analyze header

    Implements zoom-only setting of affine transform, and no image
    scaling
    
    '''
    # Copies of module-level definitions
    _dtype = header_dtype
    _data_type_codes = data_type_codes
    
    # default x flip
    default_x_flip = True

    # data scaling capabilities
    _has_data_slope = False
    _has_data_intercept = False
    
    def __init__(self,
                 binaryblock=None,
                 endianness=None,
                 check=True,
                 extra_data=None):
        ''' Initialize header from binary data block

        Parameters
        ----------
        binaryblock : {None, string} optional
            binary block to set into header.  By default, None, in
            which case we insert the default empty header block
        endianness : {None, '<','>', other endian code} string, optional
            endianness of the binaryblock.  If None, guess endianness
            from the data.
        check : bool, optional
            Whether to check content of header in initialization.
            Default is True.
        extra_data : None or dict
            Other metadata to hold in mapping from object
            
        Examples
	--------
        >>> hdr1 = AnalyzeHeader() # an empty header
        >>> hdr1.endianness == native_code
        True
        >>> hdr1.get_data_shape()
        (0,)
        >>> hdr1.set_data_shape((1,2,3)) # now with some content
        >>> hdr1.get_data_shape()
        (1, 2, 3)

        We can set the binary block directly via this initialization.
        Here we get it from the header we have just made
        
        >>> binblock2 = hdr1.binaryblock
        >>> hdr2 = AnalyzeHeader(binblock2)
        >>> hdr2.get_data_shape()
        (1, 2, 3)

        Empty headers are native endian by default

        >>> hdr2.endianness == native_code
        True

        You can pass valid opposite endian headers with the
        ``endianness`` parameter. Even empty headers can have
        endianness
        
        >>> hdr3 = AnalyzeHeader(endianness=swapped_code)
        >>> hdr3.endianness == swapped_code
        True

        If you do not pass an endianness, and you pass some data, we
        will try to guess from the passed data.

        >>> binblock3 = hdr3.binaryblock
        >>> hdr4 = AnalyzeHeader(binblock3)
        >>> hdr4.endianness == swapped_code
        True
        '''
        if extra_data is None:
            extra_data = {}
        self.extra_data = extra_data
        if binaryblock is None:
            self._header_data = self._empty_headerdata(endianness)
            return
        # check size
        if len(binaryblock) != self._dtype.itemsize:
            raise HeaderDataError('Binary block is wrong size')
        hdr = np.ndarray(shape=(),
                         dtype=self._dtype,
                         buffer=binaryblock)
        if endianness is None:
            endianness = self._guessed_endian(hdr)
        else:
            endianness = endian_codes[endianness]
        if endianness != native_code:
            dt = self._dtype.newbyteorder(endianness)
            hdr = np.ndarray(shape=(),
                             dtype=dt,
                             buffer=binaryblock)
        self._header_data = hdr.copy()
        if check:
            self.check_fix()
        return

    @property
    def binaryblock(self):
        ''' binary block of data as string

        Returns
        -------
        binaryblock : string
            string giving binary data block

        Examples
        --------
        >>> # Make default empty header
        >>> hdr = AnalyzeHeader()
        >>> len(hdr.binaryblock)
        348
        '''
        return self._header_data.tostring()

    @property
    def endianness(self):
        ''' endian code of binary data

        The endianness code gives the current byte order
        interpretation of the binary data.

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> code = hdr.endianness
        >>> code == native_code
        True

        Notes
        -----
        Endianness gives endian interpretation of binary data. It is
        read only because the only common use case is to set the
        endianness on initialization, or occasionally byteswapping the
        data - but this is done via the as_byteswapped method
        '''
        if self._header_data.dtype.isnative:
            return native_code
        return swapped_code
    
    @property
    def header_data(self):
        ''' header data, with data fields

        Examples
        --------
        >>> hdr1 = AnalyzeHeader() # an empty header
        >>> sz = hdr1.header_data['sizeof_hdr']
        '''
        return self._header_data

    def __eq__(self, other):
        ''' equality between two headers defined by mapping
        
        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr2 = AnalyzeHeader()
        >>> hdr == hdr2
        True
        >>> hdr3 = AnalyzeHeader(endianness=swapped_code)
        >>> hdr == hdr3
        True
        >>> hdr3.set_data_shape((1,2,3))
        >>> hdr == hdr3
        False
        >>> hdr4 = AnalyzeHeader()
        >>> hdr == hdr4
        True
        >>> hdr4['funny key'] = 0
        >>> hdr == hdr4
        False
        >>> hdr['funny key'] = 0
        >>> hdr == hdr4
        True
        '''
        this_end = self.endianness
        this_bb = self.binaryblock
        if this_end == other.endianness:
            return (this_bb == other.binaryblock and
                    self.extra_data == other.extra_data)
        other_bb = other._header_data.byteswap().tostring()
        return (this_bb == other_bb and
                self.extra_data == other.extra_data)
        
    def __ne__(self, other):
        ''' equality between two headers defined by ``header_data``

        For examples, see ``__eq__`` method docstring
        '''
        return not self == other

    def __getitem__(self, item):
        ''' Return values from header data

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr['sizeof_hdr'] == 348
        True
        '''
        if item in self._dtype.names:
            return self._header_data[item]
        return self.extra_data[item]
    
    def __setitem__(self, item, value):
        ''' Set values in header data

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr['descrip'] = 'description'
        >>> str(hdr['descrip'])
        'description'
        '''
        if item in self._dtype.names:
            self._header_data[item] = value
            return
        self.extra_data[item] = value

    def __iter__(self):
        return self.iterkeys()
            
    def keys(self):
        ''' Return keys from header data and extra data'''
        return list(self._dtype.names) + self.extra_data.keys()
    
    def values(self):
        ''' Return values from header data and extra data'''
        data = self._header_data
        return ([data[key] for key in self._dtype.names] + \
                    self.extra_data.values())

    def items(self):
        ''' Return items from header data and extra data'''
        return zip(self.keys(), self.values())

    def iterkeys(self):
        return iter(self.keys())

    def itervalues(self):
        return iter(self.values())

    def iteritems(self):
        return iter(self.items())

    def update(self, other):
        for key, value in other.iteritems():
            self[key] = value

    def check_fix(self,
              logger=imageglobals.logger,
              error_level=imageglobals.error_level):
        ''' Check header data with checks '''
        battrun = BatteryRunner(self.__class__._get_checks())
        self, reports = battrun.check_fix(self)
        for report in reports:
            report.log_raise(logger, error_level)

    @classmethod
    def diagnose_binaryblock(klass, binaryblock, endianness=None):
        ''' Run checks over header binary data, return string '''
        hdr = klass(binaryblock, endianness=endianness, check=False)
        battrun = BatteryRunner(klass._get_checks())
        reports = battrun.check_only(hdr)
        return '\n'.join([report.message
                          for report in reports if report.message])
                                         
    def _guessed_endian(self, hdr):
        ''' Guess intended endianness from mapping-like ``hdr``

        Parameters
        ----------
        hdr : mapping-like
           hdr for which to guess endianness

        Returns
        -------
        endianness : {'<', '>'}
           Guessed endianness of header

        Examples
        --------
        Zeros header, no information, guess native

        >>> hdr = AnalyzeHeader()
        >>> hdr_data = np.zeros((), dtype=header_dtype)
        >>> hdr._guessed_endian(hdr_data) == native_code
        True

        A valid native header is guessed native

        >>> hdr_data = hdr.header_data.copy()
        >>> hdr._guessed_endian(hdr_data) == native_code
        True

        And, when swapped, is guessed as swapped

        >>> sw_hdr_data = hdr_data.byteswap(swapped_code)
        >>> hdr._guessed_endian(sw_hdr_data) == swapped_code
        True

        The algorithm is as follows:

        First, look at the first value in the ``dim`` field; this
        should be between 0 and 7.  If it is between 1 and 7, then
        this must be a native endian header.

        >>> hdr_data = np.zeros((), dtype=header_dtype) # blank binary data
        >>> hdr_data['dim'][0] = 1
        >>> hdr._guessed_endian(hdr_data) == native_code
        True
        >>> hdr_data['dim'][0] = 6
        >>> hdr._guessed_endian(hdr_data) == native_code
        True
        >>> hdr_data['dim'][0] = -1
        >>> hdr._guessed_endian(hdr_data) == swapped_code
        True

        If the first ``dim`` value is zeros, we need a tie breaker.
        In that case we check the ``sizeof_hdr`` field.  This should
        be 348.  If it looks like the byteswapped value of 348,
        assumed swapped.  Otherwise assume native.

        >>> hdr_data = np.zeros((), dtype=header_dtype) # blank binary data
        >>> hdr._guessed_endian(hdr_data) == native_code
        True
        >>> hdr_data['sizeof_hdr'] = 1543569408
        >>> hdr._guessed_endian(hdr_data) == swapped_code
        True
        >>> hdr_data['sizeof_hdr'] = -1
        >>> hdr._guessed_endian(hdr_data) == native_code
        True

        This is overridden by the ``dim``[0] value though:
        
        >>> hdr_data['sizeof_hdr'] = 1543569408
        >>> hdr_data['dim'][0] = 1
        >>> hdr._guessed_endian(hdr_data) == native_code
        True
        '''
        dim0 = int(hdr['dim'][0])
        if dim0 == 0:
            if hdr['sizeof_hdr'] == 1543569408:
                return swapped_code
            return native_code
        elif 1<=dim0<=7:
            return native_code
        return swapped_code

    def _empty_headerdata(self, endianness=None):
        ''' Return header data for empty header with given endianness
        '''
        dt = self._dtype
        if endianness is not None:
            endianness = endian_codes[endianness]
            dt = dt.newbyteorder(endianness)
        hdr_data = np.zeros((), dtype=dt)
        hdr_data['sizeof_hdr'] = 348
        hdr_data['dim'] = 1
        hdr_data['dim'][0] = 0        
        hdr_data['pixdim'] = 1
        hdr_data['datatype'] = 16 # float32
        hdr_data['bitpix'] = 32
        return hdr_data

    @classmethod
    def from_fileobj(klass, fileobj, endianness=None, check=True):
        ''' Return read header with given or guessed endiancode

        Parameters
        ----------
        fileobj : file-like object
           Needs to implement ``read`` method
        endianness : None or endian code, optional
           Code specifying endianness of read data

        Returns
        -------
        hdr : AnalyzeHeader object
           AnalyzeHeader object initialized from data in fileobj
           
        Examples
        --------
        >>> import StringIO
        >>> hdr = AnalyzeHeader()
        >>> fileobj = StringIO.StringIO(hdr.binaryblock)
        >>> fileobj.seek(0)
        >>> hdr2 = AnalyzeHeader.from_fileobj(fileobj)
        >>> hdr2.binaryblock == hdr.binaryblock
        True

        You can write to the resulting object data

        >>> hdr2.header_data['dim'][1] = 1
        '''
        raw_str = fileobj.read(klass._dtype.itemsize)
        return klass(raw_str, endianness, check)

    @classmethod
    def from_mapping(klass, mapping, endianness=None, check=True):
        ''' Return header constructed from mapping object

        Parameters
        ----------
        mapping : mapping
           object implementing iteritems
        endianness : None or string
           Endianness for output header.  None (default) gives native
        check : bool
           Whether to check this is a valid header, default is True

        Returns
        -------
        hdr : header object

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr2 = AnalyzeHeader.from_mapping({})
        >>> hdr2 == hdr
        True
        >>> hdr2 = AnalyzeHeader.from_mapping({}, swapped_code)
        >>> hdr2 == hdr
        True
        >>> hdr2 = AnalyzeHeader.from_mapping(dict(hdr.items()))
        >>> hdr2 == hdr
        True
        >>> hdr2 =  AnalyzeHeader.from_mapping({'unlikely key':'yes'})
        >>> hdr2 == hdr
        False
        >>> hdr['unlikely key'] = 'yes'
        >>> hdr2 == hdr
        True
        >>> hdr2 =  AnalyzeHeader.from_mapping({'datatype':0})
        Traceback (most recent call last):
           ...
        HeaderDataError: data code not supported
        >>> hdr2 =  AnalyzeHeader.from_mapping({'datatype':0}, check=False)
        '''
        hdr = klass(endianness=endianness)
        hdr.update(mapping)
        if check:
            hdr.check_fix()
        return hdr

    def write_header_to(self, fileobj):
        ''' Write header to fileobj

        Write starts at fileobj current file position.  Only the
        canonical (binary) part of the header is written, not any extra
        metadata not in the binary part.
        
        Parameters
        ----------
        fileobj : file-like object
           Should implement ``write`` method

        Returns
        -------
        None

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> import StringIO
        >>> str_io = StringIO.StringIO()
        >>> hdr.write_header_to(str_io)
        >>> hdr.binaryblock == str_io.getvalue()
        True
        '''
        fileobj.write(self.binaryblock)

    def get_data_dtype(self):
        ''' Get numpy dtype for data

        For examples see ``set_data_dtype``
        '''
        code = int(self._header_data['datatype'])
        dtype = self._data_type_codes.dtype[code]
        return dtype.newbyteorder(self.endianness)
    
    def set_data_dtype(self, datatype):
        ''' Set numpy dtype for data from code or dtype or type
        
        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.set_data_dtype(np.uint8)
        >>> hdr.get_data_dtype()
        dtype('uint8')
        >>> hdr.set_data_dtype(np.dtype(np.uint8))
        >>> hdr.get_data_dtype()
        dtype('uint8')
        >>> hdr.set_data_dtype('implausible')
        Traceback (most recent call last):
           ...
        HeaderDataError: data dtype "implausible" not recognized
        >>> hdr.set_data_dtype('none')
        Traceback (most recent call last):
           ...
        HeaderDataError: data dtype "none" known but not supported
        >>> hdr.set_data_dtype(np.void)
        Traceback (most recent call last):
           ...
        HeaderDataError: data dtype "<type 'numpy.void'>" known but not supported
        '''
        try:
            code = self._data_type_codes[datatype]
        except KeyError:
            raise HeaderDataError(
                'data dtype "%s" not recognized' % datatype)
        dtype = self._data_type_codes.dtype[code]
        # test for void, being careful of user-defined types
        if dtype.type is np.void and not dtype.fields:
            raise HeaderDataError(
                'data dtype "%s" known but not supported' % datatype)
        self._header_data['datatype'] = code
        self._header_data['bitpix'] = dtype.itemsize * 8

    def get_data_shape(self):
        ''' Get shape of data

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.get_data_shape()
        (0,)
        >>> hdr.set_data_shape((1,2,3))
        >>> hdr.get_data_shape()
        (1, 2, 3)

        Expanding number of dimensions gets default zooms

        >>> hdr.get_zooms()
        (1.0, 1.0, 1.0)
        '''
        dims = self._header_data['dim']
        ndims = dims[0]
        if ndims == 0:
            return 0,
        return tuple(int(d) for d in dims[1:ndims+1])

    def set_data_shape(self, shape):
        ''' Set shape of data '''
        dims = self._header_data['dim']
        prev_ndims = dims[0]
        ndims = len(shape)
        dims[:] = 1
        dims[0] = ndims        
        dims[1:ndims+1] = shape
        
    def as_byteswapped(self, endianness):
        ''' return new byteswapped header object with given ``endianness``

        Guaranteed to make a copy even if endianness is the same as
        the current endianness.

        Parameters
        ----------
        endianness : string
           endian code to which to swap.  

        Returns
        -------
        hdr : header object
           hdr object with given endianness

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.endianness == native_code
        True
        >>> bs_hdr = hdr.as_byteswapped(swapped_code)
        >>> bs_hdr.endianness == swapped_code
        True
        >>> bs_hdr is hdr
        False
        >>> bs_hdr == hdr
        True
        
        If you write to the resulting byteswapped data, it does not
        change the original.

        >>> bs_hdr.header_data['dim'][1] = 2
        >>> bs_hdr == hdr
        False

        If you swap to the same endianness, it returns a copy

        >>> nbs_hdr = hdr.as_byteswapped(native_code)
        >>> nbs_hdr.endianness == native_code
        True
        >>> nbs_hdr is hdr
        False
        '''
        endianness = endian_codes[endianness]
        if endianness == self.endianness:
            return self.__class__(
                self.binaryblock,
                self.endianness, check=False)
        hdr_data = self._header_data.byteswap()
        return self.__class__(hdr_data.tostring(),
                              endianness,
                              check=False)

    def __str__(self):
        ''' Return string representation for printing '''
        summary = "%s object, endian='%s'" % (self.__class__,
                                              self.endianness)
        return '\n'.join(
            [summary,
             pretty_mapping(self, hdr_getterfunc)])

    def get_base_affine(self):
        ''' Get affine from basic (shared) header fields

        Note that we get the translations from the center of the
        image.

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.set_data_shape((3, 5, 7))
        >>> hdr.set_zooms((3, 2, 1))
        >>> hdr.default_x_flip
        True
        >>> hdr.get_base_affine() # from center of image
        array([[-3.,  0.,  0.,  3.],
               [ 0.,  2.,  0., -4.],
               [ 0.,  0.,  1., -3.],
               [ 0.,  0.,  0.,  1.]])
        >>> hdr.set_data_shape((3, 5))
        >>> hdr.get_base_affine()
        array([[-3.,  0.,  0.,  3.],
               [ 0.,  2.,  0., -4.],
               [ 0.,  0.,  1., -0.],
               [ 0.,  0.,  0.,  1.]])
        >>> hdr.set_data_shape((3, 5, 7))
        >>> hdr.get_base_affine() # from center of image
        array([[-3.,  0.,  0.,  3.],
               [ 0.,  2.,  0., -4.],
               [ 0.,  0.,  1., -3.],
               [ 0.,  0.,  0.,  1.]])
        '''
        hdr = self._header_data
        zooms = (hdr['pixdim'][1:4].copy())
        if self.default_x_flip:
            zooms[0] *= -1
        # Get translations from center of image
        origin = (hdr['dim'][1:4]-1) / 2.0
        aff = np.eye(4)
        aff[:3,:3] = np.diag(zooms)
        aff[:3,-1] = -origin * zooms
        return aff

    get_best_affine = get_base_affine
    
    def get_zooms(self):
        ''' Get zooms from header

        Returns
        -------
        z : tuple
           tuple of header zoom values

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.get_zooms()
        ()
        >>> hdr.set_data_shape((1,2))
        >>> hdr.get_zooms()
        (1.0, 1.0)
        >>> hdr.set_zooms((3, 4))
        >>> hdr.get_zooms()
        (3.0, 4.0)
        '''
        hdr = self._header_data
        dims = hdr['dim']
        ndim = dims[0]
        if ndim == 0:
            return ()
        pixdims = hdr['pixdim']
        return tuple(pixdims[1:ndim+1])
    
    def set_zooms(self, zooms):
        ''' Set zooms into header fields

        See docstring for ``get_zooms`` for examples
        '''
        hdr = self._header_data
        dims = hdr['dim']
        ndim = dims[0]
        zooms = np.asarray(zooms)
        if len(zooms) != ndim:
            raise HeaderDataError('Expecting %d zoom values for ndim %d'
                                  % (ndim, ndim))
        if np.any(zooms < 0):
            raise HeaderDataError('zooms must be positive')
        pixdims = hdr['pixdim']
        pixdims[1:ndim+1] = zooms[:]
        
    def get_datatype(self, code_repr='label'):
        ''' Return representation of datatype code

        This method returns the datatype code, or a string label for the
        code.  Usually you are more interested in the data dtype.  To do
        that more useful thing, use ``get_data_dtype``
        
        Parameters
        ----------
        code_repr : string
           string giving output form of datatype code representation.
           Default is 'label'; use 'code' for integer representation.

        Returns
        -------
        datatype_code : string or integer
            string label for datatype code or code

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr['datatype'] = 4 # int16
        >>> hdr.get_datatype()
        'int16'
        '''
        return self._get_code_field(
            code_repr,
            'datatype',
            self._data_type_codes)

    def read_raw_data(self, fileobj):
        ''' Read raw (unscaled) data from ``fileobj``

        Parameters
        ----------
        fileobj : file-like
           Must be open, and implement ``read`` and ``seek`` methods

	Returns
	-------
	arr : array-like
	   an array like object (that might be an ndarray),
	   implementing at least slicing.
	'''
        dtype = self.get_data_dtype()
        shape = self.get_data_shape()
        offset = int(self._header_data['vox_offset'])
        return array_from_file(shape, dtype, fileobj, offset)
        
    def read_data(self, fileobj):
        ''' Read data from ``fileobj``

        Parameters
        ----------
        fileobj : file-like
           Must be open, and implement ``read`` and ``seek`` methods

	Returns
	-------
	arr : array-like
	   an array like object (that might be an ndarray),
	   implementing at least slicing.

        Notes
        -----
        The AnalyzeHeader cannot do integer scaling.
	'''
	return self.read_raw_data(fileobj)

    def write_raw_data(self, data, fileobj):
        ''' Write ``data`` to ``fileobj`` coercing to header dtype
        
        Parameters
        ----------
        data : array-like
           data to write; should match header defined shape.  Data is
           coerced to dtype matching header by simple ``astype``.
        fileobj : file-like object
           Object with file interface, implementing ``write`` and ``seek``
        '''
        data = np.asarray(data)
        self._prepare_write(data, fileobj)
        out_dtype = self.get_data_dtype()
        array_to_file(data,
                      fileobj,
                      out_dtype)

    def write_data(self, data, fileobj):
        ''' Write data to ``fileobj`` doing best match to header dtype

        Parameters
        ----------
        data : array-like
           data to write; should match header defined shape
        fileobj : file-like object
           Object with file interface, implementing ``write`` and ``seek``

        Returns
        -------
        None

        Examples
        --------
        >>> hdr = AnalyzeHeader()
        >>> hdr.set_data_shape((1, 2, 3))
        >>> hdr.set_data_dtype(np.float64)
        >>> import StringIO
        >>> str_io = StringIO.StringIO()
        >>> data = np.arange(6).reshape(1,2,3)
        >>> hdr.write_data(data, str_io)
        >>> data.astype(np.float64).tostring('F') == str_io.getvalue()
        True
        '''
	data = np.asarray(data)
        self._cast_check(data.dtype.type)
        return self.write_raw_data(data, fileobj)

    def _prepare_write(self, data, fileobj):
        ''' Prepare fileobj for writing, check data shape '''
        shape = self.get_data_shape()
        if data.shape != shape:
            raise HeaderDataError('Data should be shape (%s)' %
                                  ', '.join(str(s) for s in shape))
        offset = int(self._header_data['vox_offset'])
        try:
            fileobj.seek(offset)
        except IOError, msg:
            if fileobj.tell() != offset:
                raise IOError(msg)

    def _cast_check(self, nptype):
        ''' Check if can cast numpy type ``nptype`` to hdr datatype

        Raise error otherwise
        '''
        out_dtype = self.get_data_dtype()
        if can_cast(nptype,
                    out_dtype.type,
                    self._has_data_intercept,
                    self._has_data_slope):
                    return
        raise HeaderTypeError('Cannot cast data to header dtype without'
                              ' large potential loss in precision')
        
    def _get_code_field(self, code_repr, fieldname, recoder):
        ''' Returns representation of field given recoder and code_repr
        '''
        code = int(self._header_data[fieldname])
        if code_repr == 'code':
            return code
        if code_repr == 'label':
            return recoder.label[code]
        raise TypeError('code_repr should be "label" or "code"')
        
    @classmethod
    def _get_checks(klass):
        ''' Return sequence of check functions for this class '''
        return (klass._chk_sizeof_hdr,
                klass._chk_datatype,
                klass._chk_bitpix,
                klass._chk_pixdims)

    ''' Check functions in format expected by BatteryRunner class '''
    
    @staticmethod
    def _chk_sizeof_hdr(hdr, fix=True):
        ret = Report(hdr, HeaderDataError)
        if hdr['sizeof_hdr'] == 348:
            return ret
        ret.problem_msg = 'sizeof_hdr should be 348'
        if fix:
            hdr['sizeof_hdr'] = 348
            ret.fix_msg = 'set sizeof_hdr to 348'
        else:
            ret.level = 30
        return ret

    @classmethod
    def _chk_datatype(klass, hdr, fix=True):
        ret = Report(hdr, HeaderDataError)
        code = int(hdr['datatype'])
        try:
            dtype = klass._data_type_codes.dtype[code]
        except KeyError:
            ret.level = 40
            ret.problem_msg = 'data code not recognized'
        else:
            if dtype.type is np.void:
                ret.level = 40
                ret.problem_msg = 'data code not supported'
        if fix:
            ret.fix_problem_msg = 'not attempting fix'
        return ret

    @classmethod
    def _chk_bitpix(klass, hdr, fix=True):
        ret = Report(hdr, HeaderDataError)
        code = int(hdr['datatype'])
        try:
            dt = klass._data_type_codes.dtype[code]
        except KeyError:
            ret.level = 10
            ret.problem_msg = 'no valid datatype to fix bitpix'
            if fix:
                ret.fix_msg = 'no way to fix bitpix'
            return ret
        bitpix = dt.itemsize * 8
        ret = Report(hdr)
        if bitpix == hdr['bitpix']:
            return ret
        ret.problem_msg = 'bitpix does not match datatype'
        if fix:
            hdr['bitpix'] = bitpix # inplace modification
            ret.fix_msg = 'setting bitpix to match datatype'
        else:
            ret.level = 10
        return ret

    @staticmethod
    def _chk_pixdims(hdr, fix=True):
        ret = Report(hdr, HeaderDataError)
        if not np.any(hdr['pixdim'][1:4] < 0):
            return ret
        ret.problem_msg = 'pixdim[1,2,3] should be positive'
        if fix:
            hdr['pixdim'][1:4] = np.abs(hdr['pixdim'][1:4])
            ret.fix_msg = 'setting to abs of pixdim values'
        else:
            ret.level = 40
        return ret


class AnalyzeImage(spatialimages.SpatialImage):
    _meta_maker = AnalyzeHeader
    def get_data(self):
        ''' Lazy load of data '''
        if not self._data is None:
            return self._data
        if not self._files:
            return None
        try:
            fname = self._files['image']
        except KeyError:
            return None
        self._data = self._metadata.read_data(allopen(fname))
        return self._data

    def get_metadata(self):
        ''' Return metadata

        Update metadata to match data, affine etc in object
        '''
        self._update_metadata()
        return self._metadata

    def get_shape(self):
        if not self._data is None:
            return self._data.shape
        return self._metadata.get_data_shape()
    
    def get_data_dtype(self):
        return self._metadata.get_data_dtype()
    
    def set_data_dtype(self, dtype):
        self._metadata.set_data_dtype(dtype)
    
    @classmethod
    def from_filespec(klass, filespec):
        files = klass.filespec_to_files(filespec)
        return klass.from_files(files)
    
    @classmethod
    def from_files(klass, files):
        fname = files['header']
        metadata = klass._meta_maker.from_fileobj(allopen(fname))
        affine = metadata.get_best_affine()
        ret =  klass(None, affine, metadata)
        ret._files = files
        return ret
    
    @classmethod
    def from_image(klass, img):
        orig_hdr = img.get_metadata()
        hdr = klass._meta_maker.from_mapping(orig_hdr)
        return klass(img.get_data(), img.get_affine(), hdr)
    
    @staticmethod
    def filespec_to_files(filespec):
        ftups = filetuples.FileTuples(
            (('header', '.hdr'),('image', '.img')),
            ignored_suffixes = ('.gz', '.bz2'))
        try:
            ftups.set_filenames(filespec)
        except filetuples.FileTuplesError:
            raise ValueError('Strange filespec "%s"' % filespec)
        files = dict(zip(('header', 'image'), ftups.get_filenames()))
        return files

    def to_filespec(self, filespec):
        ''' Write image to files given by filespec
        '''
        files = self.filespec_to_files(filespec)
        self.to_files(files)
    
    def to_files(self, files=None):
        ''' Write image to files passed, or self._files
        '''
        if files is None:
            files = self._files
            if files is None:
                raise ValueError('Need files to write data')
        data = self.get_data()
        hdr = self.get_metadata()
        hdrf = allopen(files['header'], 'wb')
        hdr.write_header_to(hdrf)
        imgf = allopen(files['image'], 'wb')
        hdr.write_data(data, imgf)
        self._files = files
        
    def _update_metadata(self):
        ''' Harmonize metadata with image data and affine

        >>> data = np.zeros((2,3,4))
        >>> affine = np.diag([1.0,2.0,3.0,1.0])
        >>> img = AnalyzeImage(data, affine)
        >>> img.get_shape()
        (2, 3, 4)
        >>> meta = img._metadata
        >>> meta.get_data_shape()
        (0,)
        >>> meta.get_zooms()
        ()
        >>> np.all(meta.get_best_affine() == np.diag([-1,1,1,1]))
        True
        >>> img._update_metadata()
        >>> meta.get_data_shape()
        (2, 3, 4)
        >>> meta.get_zooms()
        (1.0, 2.0, 3.0)
        '''
        hdr = self._metadata
        if not self._data is None:
            hdr.set_data_shape(self._data.shape)
        if not self._affine is None:
            RZS = self._affine[:3,:3]
            vox = np.sqrt(np.sum(RZS * RZS, axis=0))
            hdr['pixdim'][1:4] = vox
        

def load(filespec):
    return AnalyzeImage.from_filespec(filespec)


def save(img, filespec):
    img = AnalyzeImage.from_image(img)
    img.to_filespec(filespec)
