from __future__ import absolute_import

import random
import typing
from warnings import warn
from typing import Sequence

import cv2
from copy import deepcopy

import numpy as np

from albumentations.core.serialization import SerializableMeta, get_shortest_class_fullname
from albumentations.core.six import add_metaclass
from albumentations.core.utils import format_args

__all__ = ["to_tuple", "BasicTransform", "DualTransform", "ImageOnlyTransform", "NoOp"]

Bbox = typing.Tuple[float, float, float, float]
Keypoint = typing.Tuple[float, float, float, float]


def to_tuple(param, low=None, bias=None):
    """Convert input argument to min-max tuple
    Args:
        param (scalar, tuple or list of 2+ elements): Input value.
            If value is scalar, return value would be (offset - value, offset + value).
            If value is tuple, return value would be value + offset (broadcasted).
        low:  Second element of tuple can be passed as optional argument
        bias: An offset factor added to each element
    """
    if low is not None and bias is not None:
        raise ValueError("Arguments low and bias are mutually exclusive")

    if param is None:
        return param

    if isinstance(param, (int, float)):
        if low is None:
            param = -param, +param
        else:
            param = (low, param) if low < param else (param, low)
    elif isinstance(param, Sequence):
        param = tuple(param)
    else:
        raise ValueError("Argument param must be either scalar (int, float) or tuple")

    if bias is not None:
        return tuple(bias + x for x in param)

    return tuple(param)


@add_metaclass(SerializableMeta)
class BasicTransform:
    call_backup = None

    def __init__(self, always_apply=False, p=0.5):
        self.p = p
        self.always_apply = always_apply
        self._additional_targets = {}

        # replay mode params
        self.deterministic = False
        self.save_key = "replay"
        self.params = {}
        self.replay_mode = False
        self.applied_in_replay = False

    def __call__(self, *args, force_apply=False, **kwargs):
        if args:
            raise KeyError("You have to pass data to augmentations as named arguments, for example: aug(image=image)")
        if self.replay_mode:
            if self.applied_in_replay:
                return self.apply_with_params(self.params, **kwargs)

            return kwargs

        if (random.random() < self.p) or self.always_apply or force_apply:
            params = self.get_params()

            if self.targets_as_params:
                assert all(key in kwargs for key in self.targets_as_params), "{} requires {}".format(
                    self.__class__.__name__, self.targets_as_params
                )
                targets_as_params = {k: kwargs[k] for k in self.targets_as_params}
                params_dependent_on_targets = self.get_params_dependent_on_targets(targets_as_params)
                params.update(params_dependent_on_targets)
            if self.deterministic:
                if self.targets_as_params:
                    warn(
                        self.get_class_fullname() + " could work incorrectly in ReplayMode for other input data"
                        " because its' params depend on targets."
                    )
                kwargs[self.save_key][id(self)] = dict(
                    **deepcopy(params), **{self.save_key: self.get_reverse_args(**kwargs)}
                )
            return self.apply_with_params(params, **kwargs)

        return kwargs

    def reverse(self, data: dict, params: dict) -> typing.Any:
        reverse_params = params["params"][self.save_key]

        res = {}
        for key, arg in data.items():
            if arg is not None:
                target_function = self._get_target_reverse_function(key)
                target_dependencies = {k: params[k] for k in self.target_dependence.get(key, [])}
                res[key] = target_function(arg, **dict(**reverse_params, **target_dependencies))
            else:
                res[key] = None
        return res

    def get_reverse_args(self, **kwargs) -> dict:
        return {}

    def apply_with_params(self, params, force_apply=False, **kwargs):  # skipcq: PYL-W0613
        if params is None:
            return kwargs
        params = self.update_params(params, **kwargs)
        res = {}
        for key, arg in kwargs.items():
            if arg is not None:
                target_function = self._get_target_function(key)
                target_dependencies = {k: kwargs[k] for k in self.target_dependence.get(key, [])}
                res[key] = target_function(arg, **dict(params, **target_dependencies))
            else:
                res[key] = None
        return res

    def set_deterministic(self, flag, save_key="replay"):
        assert save_key != "params", "params save_key is reserved"
        self.deterministic = flag
        self.save_key = save_key
        return self

    def __repr__(self):
        state = self.get_base_init_args()
        state.update(self.get_transform_init_args())
        return "{name}({args})".format(name=self.__class__.__name__, args=format_args(state))

    def _get_target_function(self, key):
        transform_key = key
        if key in self._additional_targets:
            transform_key = self._additional_targets.get(key, None)

        target_function = self.targets.get(transform_key, lambda x, **p: x)
        return target_function

    def _get_target_reverse_function(self, key: str) -> typing.Callable:
        transform_key = key
        if key in self._additional_targets:
            transform_key = self._additional_targets.get(key, None)

        return self.targets_reverse.get(transform_key, lambda x, **p: x)

    def apply(self, img, **params):
        raise NotImplementedError

    def reverse_image(self, img: np.ndarray, **params) -> np.ndarray:
        raise NotImplementedError("Method reverse_image is not implemented in class " + self.__class__.__name__)

    def get_params(self):
        return {}

    @property
    def targets(self):
        # you must specify targets in subclass
        # for example: ('image', 'mask')
        #              ('image', 'boxes')
        raise NotImplementedError

    @property
    def targets_reverse(self) -> typing.Dict[str, typing.Callable]:
        raise NotImplementedError

    def update_params(self, params, **kwargs):
        if hasattr(self, "interpolation"):
            params["interpolation"] = self.interpolation
        if hasattr(self, "fill_value"):
            params["fill_value"] = self.fill_value
        if hasattr(self, "mask_fill_value"):
            params["mask_fill_value"] = self.mask_fill_value
        params.update({"cols": kwargs["image"].shape[1], "rows": kwargs["image"].shape[0]})
        return params

    @property
    def target_dependence(self):
        return {}

    def add_targets(self, additional_targets):
        """Add targets to transform them the same way as one of existing targets
        ex: {'target_image': 'image'}
        ex: {'obj1_mask': 'mask', 'obj2_mask': 'mask'}
        by the way you must have at least one object with key 'image'

        Args:
            additional_targets (dict): keys - new target name, values - old target name. ex: {'image2': 'image'}
        """
        self._additional_targets = additional_targets

    @property
    def targets_as_params(self):
        return []

    def get_params_dependent_on_targets(self, params):
        raise NotImplementedError(
            "Method get_params_dependent_on_targets is not implemented in class " + self.__class__.__name__
        )

    @classmethod
    def get_class_fullname(cls):
        return get_shortest_class_fullname(cls)

    def get_transform_init_args_names(self):
        raise NotImplementedError(
            "Class {name} is not serializable because the `get_transform_init_args_names` method is not "
            "implemented".format(name=self.get_class_fullname())
        )

    def get_base_init_args(self):
        return {"always_apply": self.always_apply, "p": self.p}

    def get_transform_init_args(self):
        return {k: getattr(self, k) for k in self.get_transform_init_args_names()}

    def _to_dict(self):
        state = {"__class_fullname__": self.get_class_fullname()}
        state.update(self.get_base_init_args())
        state.update(self.get_transform_init_args())
        return state

    def get_dict_with_id(self):
        d = self._to_dict()
        d["id"] = id(self)
        return d


class DualTransform(BasicTransform):
    """Transform for segmentation task."""

    @property
    def targets(self):
        return {
            "image": self.apply,
            "mask": self.apply_to_mask,
            "masks": self.apply_to_masks,
            "bboxes": self.apply_to_bboxes,
            "keypoints": self.apply_to_keypoints,
        }

    @property
    def targets_reverse(self) -> typing.Dict[str, typing.Callable]:
        return {
            "image": self.reverse_image,
            "mask": self.reverse_mask,
            "masks": self.reverse_masks,
            "bboxes": self.reverse_bboxes,
            "keypoints": self.reverse_keypoints,
        }

    def apply_to_bbox(self, bbox, **params):
        raise NotImplementedError("Method apply_to_bbox is not implemented in class " + self.__class__.__name__)

    def reverse_bbox(self, bbox: Bbox, **params) -> Bbox:
        raise NotImplementedError("Method reverse_bbox is not implemented in class " + self.__class__.__name__)

    def apply_to_keypoint(self, keypoint, **params):
        raise NotImplementedError("Method apply_to_keypoint is not implemented in class " + self.__class__.__name__)

    def reverse_keypoint(self, keypoint: Keypoint, **params) -> Keypoint:
        raise NotImplementedError("Method reverse_keypoint is not implemented in class " + self.__class__.__name__)

    def apply_to_bboxes(self, bboxes, **params):
        return [self.apply_to_bbox(tuple(bbox[:4]), **params) + tuple(bbox[4:]) for bbox in bboxes]

    def reverse_bboxes(self, bboxes: typing.List[typing.Tuple], **params) -> typing.List[typing.Tuple]:
        return [self.reverse_bbox(typing.cast(Bbox, tuple(bbox[:4])), **params) + tuple(bbox[4:]) for bbox in bboxes]

    def apply_to_keypoints(self, keypoints, **params):
        return [self.apply_to_keypoint(tuple(keypoint[:4]), **params) + tuple(keypoint[4:]) for keypoint in keypoints]

    def reverse_keypoints(self, keypoints: typing.List[typing.Tuple], **params) -> typing.List[typing.Tuple]:
        return [
            self.reverse_keypoint(typing.cast(Keypoint, tuple(keypoint[:4])), **params) + tuple(keypoint[4:])
            for keypoint in keypoints
        ]

    def apply_to_mask(self, img, **params):
        return self.apply(img, **{k: cv2.INTER_NEAREST if k == "interpolation" else v for k, v in params.items()})

    def reverse_mask(self, mask: np.ndarray, **params) -> np.ndarray:
        return self.reverse_image(
            mask, **{k: cv2.INTER_NEAREST if k == "interpolation" else v for k, v in params.items()}
        )

    def apply_to_masks(self, masks, **params):
        return [self.apply_to_mask(mask, **params) for mask in masks]

    def reverse_masks(self, masks: typing.List[np.ndarray], **params) -> typing.List[np.ndarray]:
        return [self.apply_to_mask(mask, **params) for mask in masks]


class ImageOnlyTransform(BasicTransform):
    """Transform applied to image only."""

    @property
    def targets(self):
        return {"image": self.apply}

    @property
    def targets_reverse(self) -> typing.Dict[str, typing.Callable]:
        return {"image": self.reverse_image}


class NoOp(DualTransform):
    """Does nothing"""

    def apply_to_keypoint(self, keypoint, **params):
        return keypoint

    def apply_to_bbox(self, bbox, **params):
        return bbox

    def apply(self, img, **params):
        return img

    def apply_to_mask(self, img, **params):
        return img

    def get_transform_init_args_names(self):
        return ()
