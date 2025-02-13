"""Transforms are common image transformations. They can be chained together
using :class:`pyrost.ComposeTransforms`. You pass a :class:`pyrost.Transform`
instance to a data container :class:`pyrost.STData`. All transform classes
are inherited from the abstract :class:`pyrost.Transform` class.

:class:`pyrost.STData` contains all the necessary data for the Speckle
Tracking algorithm, and provides a suite of data processing tools to work
with the data.

Examples:
    Load all the necessary data using a :func:`pyrost.STData.load` function.

    >>> import pyrost as rst
    >>> inp_file = rst.CXIStore('data.cxi')
    >>> data = rst.STData(input_file=inp_file)
    >>> data = data.load()
"""
from __future__ import annotations
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union
from weakref import ref
from multiprocessing import cpu_count
from tqdm.auto import tqdm
import numpy as np
from .aberrations_fit import AberrationsFit
from .data_container import DataContainer, dict_to_object
from .cxi_protocol import CXIStore, Indices
from .rst_update import SpeckleTracking
from .bin import median, median_filter, fft_convolve, ct_integrate

class Transform():
    """Abstract transform class."""

    def index_array(self, ss_idxs: np.ndarray, fs_idxs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.state_dict().__repr__()

    def __str__(self) -> str:
        return self.state_dict().__str__()

    def forward(self, inp: np.ndarray) -> np.ndarray:
        """Return a transformed image.

        Args:
            inp : Input image.

        Returns:
            Transformed image.
        """
        ss_idxs, fs_idxs = np.indices(inp.shape[-2:])
        ss_idxs, fs_idxs = self.index_array(ss_idxs, fs_idxs)
        return inp[..., ss_idxs, fs_idxs]

    def state_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

class Crop(Transform):
    """Crop transform. Crops a frame according to a region of interest.

    Attributes:
        roi : Region of interest. Comprised of four elements `[y_min, y_max,
            x_min, x_max]`.
    """
    def __init__(self, roi: Iterable[int]) -> None:
        self.roi = roi

    def index_array(self, ss_idxs: np.ndarray, fs_idxs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Filter the indices of a frame `(ss_idxs, fs_idxs)` according to
        the cropping transform.

        Args:
            ss_idxs: Slow axis indices of a frame.
            fs_idxs: Fast axis indices of a frame.

        Returns:
            A tuple of filtered frame indices `(ss_idxs, fs_idxs)`.
        """
        if ss_idxs.shape[0] == 1:
            return (ss_idxs[:, self.roi[2]:self.roi[3]],
                    fs_idxs[:, self.roi[2]:self.roi[3]])

        if ss_idxs.shape[1] == 1:
            return (ss_idxs[self.roi[0]:self.roi[1], :],
                    fs_idxs[self.roi[0]:self.roi[1], :])

        return (ss_idxs[self.roi[0]:self.roi[1], self.roi[2]:self.roi[3]],
                fs_idxs[self.roi[0]:self.roi[1], self.roi[2]:self.roi[3]])

    def state_dict(self) -> Dict[str, Any]:
        """Returns the state of the transform as a dict.

        Returns:
            A dictionary with all the attributes.
        """
        return {'roi': self.roi[:]}

class Downscale(Transform):
    """Downscale the image by a integer ratio.

    Attributes:
        scale : Downscaling integer ratio.
    """
    def __init__(self, scale: int) -> None:
        self.scale = scale

    def index_array(self, ss_idxs: np.ndarray, fs_idxs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Filter the indices of a frame `(ss_idxs, fs_idxs)` according to
        the downscaling transform.

        Args:
            ss_idxs: Slow axis indices of a frame.
            fs_idxs: Fast axis indices of a frame.

        Returns:
            A tuple of filtered frame indices `(ss_idxs, fs_idxs)`.
        """
        return (ss_idxs[::self.scale, ::self.scale], fs_idxs[::self.scale, ::self.scale])

    def state_dict(self) -> Dict[str, Any]:
        """Returns the state of the transform as a dict.

        Returns:
            A dictionary with all the attributes.
        """
        return {'scale': self.scale}

class Mirror(Transform):
    """Mirror the data around an axis.

    Attributes:
        axis : Axis of reflection.
    """
    def __init__(self, axis: int) -> None:
        if axis not in [0, 1]:
            raise ValueError('Axis must equal to 0 or 1')
        self.axis = axis

    def index_array(self, ss_idxs: np.ndarray, fs_idxs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Filter the indices of a frame `(ss_idxs, fs_idxs)` according to
        the mirroring transform.

        Args:
            ss_idxs: Slow axis indices of a frame.
            fs_idxs: Fast axis indices of a frame.

        Returns:
            A tuple of filtered frame indices `(ss_idxs, fs_idxs)`.
        """
        if self.axis == 0:
            return (ss_idxs[::-1], fs_idxs[::-1])
        if self.axis == 1:
            return (ss_idxs[:, ::-1], fs_idxs[:, ::-1])
        raise ValueError('Axis must equal to 0 or 1')

    def state_dict(self) -> Dict[str, Any]:
        """Returns the state of the transform as a dict.

        Returns:
            A dictionary with all the attributes.
        """
        return {'axis': self.axis}

class ComposeTransforms(Transform):
    """Composes several transforms together.

    Attributes:
        transforms: List of transforms.
    """
    transforms : List[Transform]

    def __init__(self, transforms: List[Transform]) -> None:
        self.transforms = []
        try:
            for transform in transforms:
                pdict = transform.state_dict()
                self.transforms.append(type(transform)(**pdict))
        except TypeError:
            raise TypeError('Invalid argument, must be a sequence of transforms.')
        else:
            if len(self.transforms) < 2:
                raise ValueError('Two or more transforms are needed to compose.')


    def __iter__(self) -> Iterator[Transform]:
        return self.transforms.__iter__()

    def __getitem__(self, idx: Indices) -> Transform:
        return self.transforms[idx]

    def index_array(self, ss_idxs: np.ndarray, fs_idxs: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Filter the indices of a frame `(ss_idxs, fs_idxs)` according to
        the composed transform.

        Args:
            ss_idxs: Slow axis indices of a frame.
            fs_idxs: Fast axis indices of a frame.

        Returns:
            A tuple of filtered frame indices `(ss_idxs, fs_idxs)`.
        """
        for transform in self:
            ss_idxs, fs_idxs = transform.index_array(ss_idxs, fs_idxs)
        return ss_idxs, fs_idxs

    def state_dict(self) -> Dict[str, Any]:
        """Returns the state of the transform as a dict.

        Returns:
            A dictionary with all the attributes.
        """
        return {'transforms': self.transforms[:]}

class STData(DataContainer):
    """Speckle tracking data container class. Needs a :class:`pyrost.CXIStore` file
    handler. Provides an interface to work with the data and contains a suite of
    tools for the R-PXST data processing pipeline. Also provides an interface to load
    from a file and save to a file any of the data attributes. The data frames can
    be tranformed using any of the :class:`pyrost.Transform` classes.

    Args:
        input_file : HDF5 or CXI file handler of input files.
        output_file : Output file handler.
        transform : Frames transform object.
        kwargs : Dictionary of the necessary and optional data attributes specified
            in :class:`pyrost.STData` notes. All the necessary attributes must be
            provided

    Raises:
        ValueError : If any of the necessary attributes specified in :class:`pyrost.STData`
            notes have not been provided.

    Notes:
        **Necessary attributes**:

        * basis_vectors : Detector basis vectors
        * data : Measured intensity frames.
        * distance : Sample-to-detector distance [m].
        * frames : List of frame indices.
        * translations : Sample's translations [m].
        * wavelength : Incoming beam's wavelength [m].
        * x_pixel_size : Pixel's size along the horizontal detector axis [m].
        * y_pixel_size : Pixel's size along the vertical detector axis [m].

        **Optional attributes**:

        * defocus_x : Defocus distance for the horizontal detector axis [m].
        * defocus_y : Defocus distance for the vertical detector axis [m].
        * good_frames : An array of good frames' indices.
        * mask : Bad pixels mask.
        * num_threads : Number of threads used in computations.
        * output_file : Output file handler.
        * phase : Phase profile of lens' aberrations.
        * pixel_aberrations : Lens' aberrations along the horizontal and
          vertical axes in pixels.
        * pixel_translations : Sample's translations in the detector's
          plane in pixels.
        * reference_image : The unabberated reference image of the sample.
        * scale_map : Huber scale map.
        * whitefield : Measured frames' white-field.
        * whitefields : Set of dynamic white-fields for each of the measured
          images.
    """
    attr_set = {'input_file'}
    init_set = {'basis_vectors', 'data', 'distance', 'frames', 'translations', 'wavelength',
                'x_pixel_size', 'y_pixel_size', 'defocus_x', 'defocus_y', 'good_frames',
                'mask', 'num_threads', 'output_file', 'phase', 'pixel_aberrations',
                'pixel_translations', 'reference_image', 'scale_map', 'transform', 'whitefield',
                'whitefields'}

    # Necessary attributes
    input_file         : CXIStore
    transform           : Transform

    # Optional attributes
    basis_vectors       : Optional[np.ndarray]
    data                : Optional[np.ndarray]
    defocus_x           : Optional[float]
    defocus_y           : Optional[float]
    distance            : Optional[np.ndarray]
    frames              : Optional[np.ndarray]
    output_file         : Optional[CXIStore]
    phase               : Optional[np.ndarray]
    pixel_aberrations   : Optional[np.ndarray]
    reference_image     : Optional[np.ndarray]
    scale_map           : Optional[np.ndarray]
    translations        : Optional[np.ndarray]
    wavelength          : Union[float, np.ndarray, None]
    whitefields         : Optional[np.ndarray]
    x_pixel_size        : Union[float, np.ndarray, None]
    y_pixel_size        : Union[float, np.ndarray, None]

    # Automatially generated attributes
    good_frames         : Optional[np.ndarray]
    mask                : Optional[np.ndarray]
    num_threads         : Optional[int]
    pixel_translations  : Optional[np.ndarray]
    whitefield          : Optional[np.ndarray]

    def __init__(self, input_file: CXIStore, output_file: Optional[CXIStore]=None,
                 transform: Optional[Transform]=None, **kwargs: Union[int, float, np.ndarray]) -> None:
        super(STData, self).__init__(input_file=input_file, output_file=output_file,
                                     transform=transform, **kwargs)

        self._init_functions(num_threads=lambda: np.clip(1, 64, cpu_count()))
        if self.shape[0] > 0:
            self._init_functions(good_frames=lambda: np.arange(self.shape[0]))
        if self._isdata:
            self._init_functions(mask=lambda: np.ones(self.shape, dtype=bool))
            func = lambda: median(inp=self.data[self.good_frames], axis=0,
                                  mask=self.mask[self.good_frames],
                                  num_threads=self.num_threads)
            self._init_functions(whitefield=func)
        if self._isdefocus:
            self._init_functions(defocus_y=lambda: self.get('defocus_x', None),
                                 pixel_translations=self._pixel_translations)

        self._init_attributes()

    @property
    def _isdata(self) -> bool:
        return self.data is not None

    @property
    def _isdefocus(self) -> bool:
        return self.defocus_x is not None

    @property
    def _isphase(self) -> bool:
        return not self.pixel_aberrations is None and not self.phase is None

    @property
    def shape(self) -> Tuple[int, int, int]:
        shape = [0, 0, 0]
        for attr, data in self.items():
            if attr in self.input_file.protocol and data is not None:
                kind = self.input_file.protocol.get_kind(attr)
                if kind == 'sequence':
                    shape[0] = data.shape[0]

        for attr, data in self.items():
            if attr in self.input_file.protocol and data is not None:
                kind = self.input_file.protocol.get_kind(attr)
                if kind == 'frame':
                    shape[1:] = data.shape

        for attr, data in self.items():
            if attr in self.input_file.protocol and data is not None:
                kind = self.input_file.protocol.get_kind(attr)
                if kind == 'stack':
                    shape[:] = data.shape
        return tuple(shape)

    def _pixel_translations(self) -> np.ndarray:
        pixel_translations = (self.translations[:, None] * self.basis_vectors).sum(axis=-1)
        mag = np.abs(self.distance / np.array([self.defocus_y, self.defocus_x]))
        pixel_translations *= mag / (self.basis_vectors**2).sum(axis=-1)
        pixel_translations -= pixel_translations[0]
        pixel_translations -= pixel_translations.mean(axis=0)
        return pixel_translations

    def pixel_map(self, dtype: np.dtype=np.float64) -> np.ndarray:
        """Return a preliminary pixel mapping.

        Args:
            dtype : The data type of the output pixel mapping.

        Returns:
            Pixel mapping array.
        """
        with self.input_file:
            self.input_file.update_indices()
            shape = self.input_file.read_shape()

        # Check if STData is integrated
        if self.shape[1] == 1:
            shape = (1, shape[1])
        if self.shape[2] == 1:
            shape = (shape[0], 1)

        ss_idxs, fs_idxs = np.indices(shape, dtype=dtype)
        if self.transform:
            ss_idxs, fs_idxs = self.transform.index_array(ss_idxs, fs_idxs)
        pixel_map = np.stack((ss_idxs, fs_idxs))

        if self._isdefocus:
            if self.defocus_y < 0.0:
                pixel_map = np.flip(pixel_map, axis=1)
            if self.defocus_x < 0.0:
                pixel_map = np.flip(pixel_map, axis=2)
        return np.asarray(pixel_map, order='C')

    @dict_to_object
    def load(self, attributes: Union[str, List[str], None]=None, idxs: Optional[Iterable[int]]=None,
             processes: int=1, verbose: bool=True) -> STData:
        """Load data attributes from the input files in `input_file` file handler object.

        Args:
            attributes : List of attributes to load. Loads all the data attributes
                contained in the file(s) by default.
            idxs : List of frame indices to load.
            processes : Number of parallel workers used during the loading.
            verbose : Set the verbosity of the loading process.

        Raises:
            ValueError : If attribute is not existing in the input file(s).
            ValueError : If attribute is invalid.

        Returns:
            New :class:`STData` object with the attributes loaded.
        """
        with self.input_file:
            self.input_file.update_indices()
            shape = self.input_file.read_shape()

            if attributes is None:
                attributes = [attr for attr in self.input_file.keys()
                              if attr in self.init_set]
            else:
                attributes = self.input_file.protocol.str_to_list(attributes)

            if idxs is None:
                idxs = self.input_file.indices()
            data_dict = {'frames': idxs, 'good_frames': None}

            for attr in attributes:
                if attr not in self.input_file.keys():
                    raise ValueError(f"No '{attr}' attribute in the input files")
                if attr not in self.init_set:
                    raise ValueError(f"Invalid attribute: '{attr}'")

                if self.transform and shape[0] * shape[1]:
                    ss_idxs, fs_idxs = np.indices(shape)
                    ss_idxs, fs_idxs = self.transform.index_array(ss_idxs, fs_idxs)
                    data = self.input_file.load_attribute(attr, idxs=idxs, ss_idxs=ss_idxs, fs_idxs=fs_idxs,
                                                          processes=processes, verbose=verbose)
                else:
                    data = self.input_file.load_attribute(attr, idxs=idxs, processes=processes,
                                                          verbose=verbose)

                data_dict[attr] = data

        return data_dict

    def save(self, attributes: Union[str, List[str], None]=None,
             mode: str='append', idxs: Optional[Iterable[int]]=None) -> None:
        """Save data arrays of the data attributes contained in the container to
        an output file.

        Args:
            attributes : List of attributes to save. Saves all the data attributes
                contained in the container by default.
            mode : Writing mode:

                * `append` : Append the data array to already existing dataset.
                * `insert` : Insert the data under the given indices `idxs`.
                * `overwrite` : Overwrite the existing dataset.

            idxs : A set of frame indices where the data is saved if `mode` is
                `insert`.

            verbose : Set the verbosity of the loading process.

        Raises:
            ValueError : If `output_file` is not defined inside the container.
        """
        if self.output_file is None:
            raise ValueError("'output_file' is not defined inside the container")

        if attributes is None:
            attributes = list(self.contents())

        with self.output_file:
            for attr in self.output_file.protocol.str_to_list(attributes):
                data = self.get(attr)
                if attr in self.output_file.protocol and data is not None:
                    kind = self.output_file.protocol.get_kind(attr)

                    if kind in ['stack', 'sequence']:
                        data = data[self.good_frames]

                    self.output_file.save_attribute(attr, np.asarray(data), mode=mode, idxs=idxs)

    @dict_to_object
    def clear(self, attributes: Union[str, List[str], None]=None) -> STData:
        """Clear the container.

        Args:
            attributes : List of attributes to clear in the container.

        Returns:
            New :class:`STData` object with the attributes cleared.
        """
        if attributes is None:
            attributes = self.keys()
        data_dict = {}
        for attr in self.input_file.protocol.str_to_list(attributes):
            data = self.get(attr)
            if attr in self and isinstance(data, np.ndarray):
                data_dict[attr] = None
        return data_dict

    @dict_to_object
    def update_output_file(self, output_file: CXIStore) -> STData:
        """Return a new :class:`STData` object with the new output
        file handler.

        Args:
            output_file : A new output file handler.

        Returns:
            New :class:`STData` object with the new output file
            handler.
        """
        return {'output_file': output_file}

    @dict_to_object
    def integrate_data(self, axis: int=0) -> STData:
        """Return a new :class:`STData` object with the `data` summed
        over the `axis`. Clear all the 2D and 3D data attributes inside the
        container.

        Args:
            axis : Axis along which a sum is performed.

        Returns:
            New :class:`STData` object with the stack of measured
            frames integrated along the given axis.
        """
        if self._isdata:
            data_dict = {}

            for attr, data in self.items():
                if attr in self.input_file.protocol and data is not None:
                    kind = self.input_file.protocol.get_kind(attr)
                    if kind in ['stack', 'frame']:
                        data_dict[attr] = None

            data_dict['data'] = (self.data * self.mask).sum(axis=axis - 2, keepdims=True)

            return data_dict

        raise AttributeError('data has not been loaded')

    @dict_to_object
    def mask_frames(self, good_frames: Optional[Iterable[int]]=None) -> STData:
        """Return a new :class:`STData` object with the updated good frames
        mask. Mask empty frames by default.

        Args:
            good_frames : List of good frames' indices. Masks empty frames
                if not provided.

        Returns:
            New :class:`STData` object with the updated `good_frames` and
            `whitefield`.
        """
        if good_frames is None:
            good_frames = np.where(self.data.sum(axis=(1, 2)) > 0)[0]
        return {'good_frames': np.asarray(good_frames), 'whitefield': None}

    @dict_to_object
    def update_mask(self, method: str='perc-bad', pmin: float=0., pmax: float=99.99,
                    vmin: int=0, vmax: int=65535, update: str='reset') -> STData:
        """Return a new :class:`STData` object with the updated bad pixels
        mask.

        Args:
            method : Bad pixels masking methods:

                * `no-bad` (default) : No bad pixels.
                * `range-bad` : Mask the pixels which values lie outside
                  of (`vmin`, `vmax`) range.
                * `perc-bad` : Mask the pixels which values lie outside
                  of the (`pmin`, `pmax`) percentiles.

            vmin : Lower intensity bound of 'range-bad' masking method.
            vmax : Upper intensity bound of 'range-bad' masking method.
            pmin : Lower percentage bound of 'perc-bad' masking method.
            pmax : Upper percentage bound of 'perc-bad' masking method.
            update : Multiply the new mask and the old one if `multiply`,
                use the new one if `reset`.

        Returns:
            New :class:`STData` object with the updated `mask`.
        """
        if update == 'reset':
            data = self.data
        elif update == 'multiply':
            data = self.data * self.mask
        else:
            raise ValueError(f'Invalid update keyword: {update:s}')

        if method == 'no-bad':
            mask = np.ones(self.shape, dtype=bool)
        elif method == 'range-bad':
            mask = (data >= vmin) & (data < vmax)
        elif method == 'perc-bad':
            average = median_filter(data, (1, 3, 3), num_threads=self.num_threads)
            offsets = (data.astype(np.int32) - average.astype(np.int32))
            mask = (offsets >= np.percentile(offsets, pmin)) & \
                   (offsets <= np.percentile(offsets, pmax))
        else:
            ValueError('invalid method argument')

        if update == 'reset':
            return {'mask': mask, 'whitefield': None}
        if update == 'multiply':
            return {'mask': mask * self.mask, 'whitefield': None}
        raise ValueError(f'Invalid update keyword: {update}')

    @dict_to_object
    def update_transform(self, transform: Transform) -> STData:
        """Return a new :class:`STData` object with the updated transform object.

        Args:
            transform : New :class:`Transform` object.

        Returns:
            New :class:`STData` object with the updated transform object.
        """
        data_dict = {'transform': transform}

        if self.transform is None:
            for attr, data in self.items():
                if attr in self.input_file.protocol and data is not None:
                    kind = self.input_file.protocol.get_kind(attr)
                    if kind in ['stack', 'frame']:
                        data = transform.forward(data)
                    data_dict[attr] = data

            return data_dict

        for attr, data in self.items():
            if attr in self.input_file.protocol and data is not None:
                kind = self.input_file.protocol.get_kind(attr)
                if kind in ['stack', 'frame']:
                    data_dict[attr] = None
        return data_dict

    @dict_to_object
    def update_whitefield(self) -> STData:
        """Return a new :class:`STData` object with the updated `whitefield`.

        Returns:
            New :class:`STData` object with the updated `whitefield`.
        """
        return {'whitefield': None}

    @dict_to_object
    def update_defocus(self, defocus_x: float, defocus_y: Optional[float]=None) -> STData:
        """Return a new :class:`STData` object with the updated defocus
        distances `defocus_x` and `defocus_y` for the horizontal and
        vertical detector axes accordingly. Update `pixel_translations`
        based on the new defocus distances.

        Args:
            defocus_x : Defocus distance for the horizontal detector axis [m].
            defocus_y : Defocus distance for the vertical detector axis [m].
                Equals to `defocus_x` if it's not provided.

        Returns:
            New :class:`STData` object with the updated `defocus_y`,
            `defocus_x`, and `pixel_translations`.
        """
        if defocus_y is None:
            defocus_y = defocus_x
        return {'defocus_y': defocus_y, 'defocus_x': defocus_x,
                'pixel_translations': None}

    def import_st(self, st_obj: SpeckleTracking) -> None:
        """Update `pixel_aberrations`, `phase`, `reference_image`, and `scale_map`
        based on the data from `st_obj` object. `st_obj` must be derived from this
        data container, an error is raised otherwise.

        Args:
            st_obj : :class:`SpeckleTracking` object derived from this
                data container.

        Raises:
            ValueError : If `st_obj` wasn't derived from this data container.
        """
        if st_obj.parent() is not self:
            raise ValueError("'st_obj' wasn't derived from this data container")
        # Update phase, pixel_aberrations, and reference_image
        dpm_y, dpm_x = (st_obj.pixel_map - self.pixel_map())
        dpm_y -= dpm_y.mean()
        dpm_x -= dpm_x.mean()
        self.pixel_aberrations = np.stack((dpm_y, dpm_x))

        # Calculate magnification for horizontal and vertical axes
        mag_y = np.abs((self.distance + self.defocus_y) / self.defocus_y)
        mag_x = np.abs((self.distance + self.defocus_x) / self.defocus_x)

        # Calculate the distance between the reference and the detector plane
        dist_y = self.distance * (mag_y - 1.0) / mag_y
        dist_x = self.distance * (mag_x - 1.0) / mag_x

        # dTheta = delta_pix / distance / magnification * du
        # Phase = 2 * pi / wavelength * Integrate[dTheta, delta_pix]
        phase = ct_integrate(sy_arr=self.y_pixel_size**2 / dist_y / mag_y * dpm_y,
                             sx_arr=self.x_pixel_size**2 / dist_x / mag_x * dpm_x)
        self.phase = 2.0 * np.pi / self.wavelength * phase
        self.reference_image = st_obj.reference_image
        self.scale_map = st_obj.scale_map

    def fit_phase(self, center: int=0, axis: int=1, max_order: int=2, xtol: float=1e-14,
                  ftol: float=1e-14, loss: str='cauchy') -> Dict[str, Union[float, np.ndarray]]:
        """Fit `pixel_aberrations` with the polynomial function using nonlinear
        least-squares algorithm. The function uses least-squares algorithm from
        :func:`scipy.optimize.least_squares`.

        Args:
            center : Index of the zerro scattering angle or direct beam pixel.
            axis : Axis along which `pixel_aberrations` is fitted.
            max_order : Maximum order of the polynomial model function.
            xtol : Tolerance for termination by the change of the independent
                variables.
            ftol : Tolerance for termination by the change of the cost function.
            loss : Determines the loss function. The following keyword values are
                allowed:

                * `linear` : ``rho(z) = z``. Gives a standard
                  least-squares problem.
                * `soft_l1` : ``rho(z) = 2 * ((1 + z)**0.5 - 1)``. The smooth
                  approximation of l1 (absolute value) loss. Usually a good
                  choice for robust least squares.
                * `huber` : ``rho(z) = z if z <= 1 else 2*z**0.5 - 1``. Works
                  similarly to 'soft_l1'.
                * `cauchy` (default) : ``rho(z) = ln(1 + z)``. Severely weakens
                  outliers influence, but may cause difficulties in optimization
                  process.
                * `arctan` : ``rho(z) = arctan(z)``. Limits a maximum loss on
                  a single residual, has properties similar to 'cauchy'.

        Returns:
            A dictionary with the model fit information. The following fields
            are contained:

            * `c_3` : Third order aberrations coefficient [rad / mrad^3].
            * `c_4` : Fourth order aberrations coefficient [rad / mrad^4].
            * `fit` : Array of the polynomial function coefficients of the
              pixel aberrations fit.
            * `ph_fit` : Array of the polynomial function coefficients of
              the phase aberrations fit.
            * `rel_err` : Vector of relative errors of the fit coefficients.
            * `r_sq` : ``R**2`` goodness of fit.

        See Also:
            :func:`pyrost.AberrationsFit.fit` : Full details of the aberrations
            fitting algorithm.
        """
        if not self._isphase:
            raise ValueError("'phase' is not defined inside the container.")
        return self.get_fit(center=center, axis=axis).fit(max_order=max_order,
                                                          xtol=xtol, ftol=ftol,
                                                          loss=loss)

    def defocus_sweep(self, defoci_x: np.ndarray, defoci_y: Optional[np.ndarray]=None, size: int=51,
                      hval: Optional[float]=None, extra_args: Dict[str, Union[float, bool, str]]={},
                      return_extra: bool=False, verbose: bool=True) -> Tuple[List[float], Dict[str, np.ndarray]]:
        r"""Calculate a set of reference images for each defocus in `defoci` and
        return an average R-characteristic of an image (the higher the value the
        sharper reference image is). The kernel bandwidth `hval` is automatically
        estimated by default. Return the intermediate results if `return_extra`
        is True.

        Args:
            defoci_x : Array of defocus distances along the horizontal detector axis [m].
            defoci_y : Array of defocus distances along the vertical detector axis [m].
            hval : Kernel bandwidth in pixels for the reference image update. Estimated
                with :func:`pyrost.SpeckleTracking.find_hopt` for an average defocus value
                if None.
            size : Local variance filter size in pixels.
            extra_args : Extra arguments parser to the :func:`STData.get_st` and
                :func:`SpeckleTracking.update_reference` methods. The following
                keyword values are allowed:

                * `ds_y` : Reference image sampling interval in pixels along the
                  horizontal axis. The default value is 1.0.
                * `ds_x` : Reference image sampling interval in pixels along the
                  vertical axis. The default value is 1.0.
                * `aberrations` : Add `pixel_aberrations` to `pixel_map` of
                  :class:`SpeckleTracking` object if it's True. The default value
                  is False.
                * `ff_correction` : Apply dynamic flatfield correction if it's True.
                  The default value is False.
                * `ref_method` : Choose the reference image update algorithm. The
                  following keyword values are allowed:

                  * `KerReg` : Kernel regression algorithm.
                  * `LOWESS` : Local weighted linear regression.

                  The default value is 'KerReg'.

            return_extra : Return a dictionary with the intermediate results if True.
            verbose : Set the verbosity of the process.

        Returns:
            A tuple of two items ('r_vals', 'extra'). The elements are as
            follows:

            * `r_vals` : Array of the average values of `reference_image` gradients
              squared.
            * `extra` : Dictionary with the intermediate results. Only if `return_extra`
              is True. Contains the following data:

              * reference_image : The generated set of reference profiles.
              * r_images : The set of local variance images of reference profiles.

        Notes:
            R-characteristic is called a local variance and is given by:

            .. math::
                R[i, j] = \frac{\sum_{i^{\prime} = -N / 2}^{N / 2}
                \sum_{j^{\prime} = -N / 2}^{N / 2} (I[i - i^{\prime}, j - j^{\prime}]
                - \bar{I}[i, j])^2}{\bar{I}^2[i, j]},

            where :math:`\bar{I}[i, j]` is a local mean and defined as follows:

            .. math::
                \bar{I}[i, j] = \frac{1}{N^2} \sum_{i^{\prime} = -N / 2}^{N / 2}
                \sum_{j^{\prime} = -N / 2}^{N / 2} I[i - i^{\prime}, j - j^{\prime}]

        See Also:
            :func:`pyrost.SpeckleTracking.update_reference` : reference image update
            algorithm.
        """
        if defoci_y is None:
            defoci_y = defoci_x.copy()

        ds_y = extra_args.get('ds_y', 1.0)
        ds_x = extra_args.get('ds_x', 1.0)
        aberrations = extra_args.get('aberrations', False)
        ff_correction = extra_args.get('ff_correction', False)
        ref_method = extra_args.get('ref_method', 'KerReg')

        r_vals = []
        extra = {'reference_image': [], 'r_image': []}
        kernel = np.ones(int(size)) / size
        df0_x, df0_y = defoci_x.mean(), defoci_y.mean()
        st_obj = self.update_defocus(df0_x, df0_y).get_st(ds_y=ds_y, ds_x=ds_x,
                                                          aberrations=aberrations,
                                                          ff_correction=ff_correction)
        if hval is None:
            hval = st_obj.find_hopt(method=ref_method)

        for df1_x, df1_y in tqdm(zip(defoci_x.ravel(), defoci_y.ravel()),
                               total=defoci_x.size, disable=not verbose,
                               desc='Generating defocus sweep'):
            st_obj.di_pix *= np.abs(df0_y / df1_y)
            st_obj.dj_pix *= np.abs(df0_x / df1_x)
            df0_x, df0_y = df1_x, df1_y
            st_obj.update_reference.inplace_update(hval=hval, method=ref_method)
            extra['reference_image'].append(st_obj.reference_image)
            mean = st_obj.reference_image.copy()
            mean_sq = st_obj.reference_image**2
            if st_obj.reference_image.shape[0] > size:
                mean = fft_convolve(mean, kernel, mode='reflect', axis=0,
                                    num_threads=self.num_threads)[size // 2:-size // 2]
                mean_sq = fft_convolve(mean_sq, kernel, mode='reflect', axis=0,
                                       num_threads=self.num_threads)[size // 2:-size // 2]
            if st_obj.reference_image.shape[1] > size:
                mean = fft_convolve(mean, kernel, mode='reflect', axis=1,
                                    num_threads=self.num_threads)[:, size // 2:-size // 2]
                mean_sq = fft_convolve(mean_sq, kernel, mode='reflect', axis=1,
                                       num_threads=self.num_threads)[:, size // 2:-size // 2]
            r_image = (mean_sq - mean**2) / mean**2
            extra['r_image'].append(r_image)
            r_vals.append(np.mean(r_image))

        if return_extra:
            return r_vals, extra
        return r_vals

    def get_st(self, ds_y: float=1.0, ds_x: float=1.0, aberrations: bool=False,
               ff_correction: bool=False) -> SpeckleTracking:
        """Return :class:`SpeckleTracking` object derived from the container.
        Return None if `defocus_x` or `defocus_y` doesn't exist in the container.

        Args:
            ds_y : Reference image sampling interval in pixels along the vertical
                axis.
            ds_x : Reference image sampling interval in pixels along the
                horizontal axis.
            aberrations : Add `pixel_aberrations` to `pixel_map` of
                :class:`SpeckleTracking` object if it's True.
            ff_correction : Apply dynamic flatfield correction if it's True.

        Returns:
            An instance of :class:`SpeckleTracking` derived from the container.
            None if `defocus_x` or `defocus_y` are not defined.
        """
        if not self._isdefocus:
            raise ValueError("'defocus_x' is not defined inside the container.")

        if np.issubdtype(self.data.dtype, np.uint32):
            dtypes = SpeckleTracking.dtypes_32
        else:
            dtypes = SpeckleTracking.dtypes_64

        data = np.asarray((self.mask * self.data)[self.good_frames],
                          order='C', dtype=dtypes['data'])
        whitefield = np.asarray(self.whitefield, order='C', dtype=dtypes['whitefield'])
        dij_pix = np.asarray(np.swapaxes(self.pixel_translations[self.good_frames], 0, 1),
                             order='C', dtype=dtypes['dij_pix'])

        if ff_correction and self.whitefields is not None:
            np.rint(data * np.where(self.whitefields > 0, whitefield / self.whitefields, 1.),
                    out=data, casting='unsafe')

        pixel_map = self.pixel_map(dtype=dtypes['pixel_map'])

        if aberrations:
            pixel_map += self.pixel_aberrations
            if self.scale_map is None:
                scale_map = None
            else:
                scale_map = np.asarray(self.scale_map, order='C', dtype=dtypes['scale_map'])
            return SpeckleTracking(parent=ref(self), data=data, dj_pix=dij_pix[1],
                                   di_pix=dij_pix[0], num_threads=self.num_threads,
                                   pixel_map=pixel_map, scale_map=scale_map, ds_y=ds_y,
                                   ds_x=ds_x, whitefield=whitefield)

        return SpeckleTracking(parent=ref(self), data=data, dj_pix=dij_pix[1], di_pix=dij_pix[0],
                               num_threads=self.num_threads, pixel_map=pixel_map, ds_y=ds_y,
                               ds_x=ds_x, whitefield=whitefield)

    def get_fit(self, center: int=0, axis: int=1) -> AberrationsFit:
        """Return an :class:`AberrationsFit` object for parametric regression
        of the lens' aberrations profile. Raises an error if 'defocus_x' or
        'defocus_y' is not defined.

        Args:
            center : Index of the zerro scattering angle or direct beam pixel.
            axis : Detector axis along which the fitting is performed.

        Raises:
            ValueError : If 'defocus_x' or 'defocus_y' is not defined in the
                container.

        Returns:
            An instance of :class:`AberrationsFit` class.
        """
        if not self._isphase:
            raise ValueError("'phase' or 'pixel_aberrations' are not defined inside the container.")

        data_dict = {attr: self.get(attr) for attr in AberrationsFit.attr_set if attr in self}
        if axis == 0:
            data_dict.update({attr: self.get(data_attr)
                              for attr, data_attr in AberrationsFit.y_lookup.items()})
        elif axis == 1:
            data_dict.update({attr: self.get(data_attr)
                              for attr, data_attr in AberrationsFit.x_lookup.items()})
        else:
            raise ValueError(f'invalid axis value: {axis:d}')

        data_dict['defocus'] = np.abs(data_dict['defocus'])
        if center <= self.shape[axis - 2]:
            data_dict['pixels'] = np.arange(self.shape[axis - 2]) - center
            data_dict['pixel_aberrations'] = data_dict['pixel_aberrations'][axis].mean(axis=1 - axis)
        elif center >= self.shape[axis - 2] - 1:
            data_dict['pixels'] = center - np.arange(self.shape[axis - 2])
            idxs = np.argsort(data_dict['pixels'])
            data_dict['pixel_aberrations'] = -data_dict['pixel_aberrations'][axis].mean(axis=1 - axis)[idxs]
            data_dict['pixels'] = data_dict['pixels'][idxs]
        else:
            raise ValueError('Origin must be outside of the region of interest')

        return AberrationsFit(parent=ref(self), **data_dict)

    def get_pca(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Perform the Principal Component Analysis [PCA]_ of the measured data and
        return a set of eigen flatfields (EFF).

        Returns:
            A tuple of ('cor_data', 'effs', 'eig_vals'). The elements are
            as follows:

            * `cor_data` : Background corrected stack of measured frames.
            * `effs` : Set of eigen flat-fields.
            * `eig_vals` : Corresponding eigen values for each of the eigen
              flat-fields.

        References:
            .. [PCA] Vincent Van Nieuwenhove, Jan De Beenhouwer, Francesco De Carlo,
                    Lucia Mancini, Federica Marone, and Jan Sijbers, "Dynamic intensity
                    normalization using eigen flat fields in X-ray imaging," Opt.
                    Express 23, 27975-27989 (2015).
        """
        if self._isdata:

            dtype = np.promote_types(self.whitefield.dtype, int)
            cor_data = np.zeros(self.shape, dtype=dtype)[self.good_frames]
            np.subtract(self.data[self.good_frames], self.whitefield, dtype=dtype,
                        where=self.mask[self.good_frames], out=cor_data)
            mat_svd = np.tensordot(cor_data, cor_data, axes=((1, 2), (1, 2)))
            eig_vals, eig_vecs = np.linalg.eig(mat_svd)
            effs = np.tensordot(eig_vecs, cor_data, axes=((0,), (0,)))
            return cor_data, effs, eig_vals / eig_vals.sum()

        raise AttributeError('Data has not been loaded')

    @dict_to_object
    def update_whitefields(self, method: str='median', size: int=11,
                           cor_data: Optional[np.ndarray]=None,
                           effs: Optional[np.ndarray]=None) -> STData:
        """Return a new :class:`STData` object with a new set of dynamic whitefields.
        A set of whitefields are generated by the dint of median filtering or Principal
        Component Analysis [PCA]_.

        Args:
            method : Method to generate a set of dynamic white-fields. The following
                keyword values are allowed:

                * `median` : Median `data` along the first axis.
                * `pca` : Generate a set of dynamic white-fields based on eigen flatfields
                  `effs` from the PCA. `effs` can be obtained with :func:`STData.get_pca`
                  method.

            size : Size of the filter window in pixels used for the 'median' generation
                method.
            cor_data : Background corrected stack of measured frames.
            effs : Set of Eigen flatfields used for the 'pca' generation method.

        Raises:
            ValueError : If the `method` keyword is invalid.
            AttributeError : If the `whitefield` is absent in the :class:`STData`
                container when using the 'pca' generation method.
            ValueError : If `effs` were not provided when using the 'pca' generation
                method.

        Returns:
            New :class:`STData` object with the updated `whitefields`.

        See Also:
            :func:`pyrost.STData.get_pca` : Method to generate eigen flatfields.
        """
        if self._isdata:

            if method == 'median':
                offsets = np.abs(self.data - self.whitefield)
                outliers = offsets < 3 * np.sqrt(self.whitefield)
                whitefields = median_filter(self.data, size=(size, 1, 1), mask=outliers,
                                            num_threads=self.num_threads)
            elif method == 'pca':
                if cor_data is None:
                    dtype = np.promote_types(self.whitefield.dtype, int)
                    cor_data = np.zeros(self.shape, dtype=dtype)
                    np.subtract(self.data, self.whitefield, dtype=dtype,
                                where=self.mask, out=cor_data)
                if effs is None:
                    raise ValueError('No eigen flat fields were provided')

                weights = np.tensordot(cor_data, effs, axes=((1, 2), (1, 2)))
                weights /= np.sum(effs * effs, axis=(1, 2))
                whitefields = np.tensordot(weights, effs, axes=((1,), (0,)))
                whitefields += self.whitefield
            else:
                raise ValueError('Invalid method argument')

            return {'whitefields': whitefields}

        raise ValueError('Data has not been loaded')
