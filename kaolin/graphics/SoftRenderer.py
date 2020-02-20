# Copyright (c) 2019, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# 
# 
# Soft Rasterizer (SoftRas)
# 
# Copyright (c) 2017 Hiroharu Kato
# Copyright (c) 2018 Nikos Kolotouros
# Copyright (c) 2019 Shichen Liu
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import math
from typing import Optional

import torch

from .DifferentiableRenderer import DifferentiableRenderer
from .Lighting import compute_ambient_light
from .Lighting import compute_directional_light
from .softras.rasterizer import SoftRasterizer
from .softras.soft_rasterize import soft_rasterize


class SoftRenderer(DifferentiableRenderer):
    r"""A class implementing the \emph{Soft Renderer}
        from the following ICCV 2019 paper:
            Soft Rasterizer: A differentiable renderer for image-based 3D reasoning
            Shichen Liu, Tianye Li, Weikai Chen, and Hao Li
            Link: https://arxiv.org/abs/1904.01786

    .. note::
        If you use this code, please cite the original paper in addition to Kaolin.

        .. code-block::
            @article{liu2019softras,
              title={Soft Rasterizer: A Differentiable Renderer for Image-based 3D Reasoning},
              author={Liu, Shichen and Li, Tianye and Chen, Weikai and Li, Hao},
              journal={The IEEE International Conference on Computer Vision (ICCV)},
              month = {Oct},
              year={2019}
            }

    """

    def __init__(
            self,
            image_size: int = 256,
            anti_aliasing: bool = True,
            bg_color: torch.Tensor = torch.zeros(3),
            fill_back: bool = True,
            camera_mode: str = 'projection',
            K=None, rmat=None, tvec=None,
            perspective_distort: bool = True,
            sigma_val: float = 1e-5,
            dist_func: str = 'euclidean',
            dist_eps: float = 1e-4,
            gamma_val: float = 1e-4,
            aggr_func_rgb: str = 'softmax',
            aggr_func_alpha: str = 'prod',
            texture_type: str = 'surface',
            viewing_angle: float = 30.,
            viewing_scale: float = 1.0, 
            eye: torch.Tensor = None,
            camera_direction: torch.Tensor = torch.FloatTensor([0, 0, 1]),
            near: float = 1, far: float = 100,
            light_mode: str = 'surface',
            light_intensity_ambient: float = 0.5,
            light_intensity_directional: float = 0.5,
            light_color_ambient: torch.Tensor = torch.ones(3),
            light_color_directional: torch.Tensor = torch.ones(3),
            light_direction: torch.Tensor = torch.FloatTensor([0, 1, 0]),
            device: str = 'cpu'):
        r"""Initalize the SoftRenderer object.

        NOTE: SoftRenderer works only in GPU mode!

        Args:
            image_size (int): Size of the (square) image to be rendered.
            anti_aliasing (bool): Whether or not to perform anti-aliasing
                (default: True)
            bg_color (torch.Tensor): Background color of rendered image
                (size: math:`3`, default: :math:`\left[0, 0, 0\right]`)
            fill_back (bool): Whether or not to fill color to the back
                side of each triangle as well (sometimes helps, when
                the triangles in the mesh are not properly oriented.)
                (default: True)
            camera_mode (str): Choose from among `projection`, `look`, and
                `look_at`. In the `projection` mode, the camera is at the
                origin, and its optical axis is aligned with the positive
                Z-axis. In the `look_at` mode, the object (not the camera)
                is placed at the origin. The camera "looks at" the object
                from a predefined "eye" location, which is computed from
                the `viewing_angle` (another input to this function). In
                the `look` mode, only the direction in which the camera
                needs to look is specified. It does not necessarily look
                towards the origin, as it allows the specification of a
                custom "upwards" direction (default: 'projection').
            K (torch.Tensor): Camera intrinsics matrix. Note that, unlike
                standard notation, K here is a 4 x 4 matrix (with the last
                row and last column drawn from the 4 x 4 identity matrix)
                (default: None)
            rmat (torch.Tensor): Rotation matrix (again, 4 x 4, as opposed
                to the usual 3 x 3 convention).
            tvec (torch.Tensor): Translation vector (3 x 1). Note that the
                (negative of the) tranlation is applied before rotation,
                to be consistent with the projective geometry convention
                of transforming a 3D point X by doing
                torch.matmul(R.transpose(), X - t) (default: None)
            perspective_distort (bool): Whether or not to perform perspective
                distortion (to simulate field-of-view based distortion effects)
                (default: True).
            viewing_angle (float): Angle at which the object is to be viewed
                (assumed to be in degrees!) (default: 30.)
            camera_direction (float): Direction in which the camera is facing
                (used only in the `look` and `look_at` modes) (default:
                :math:`[0, 0, 1]`)
            near (float): Near clipping plane (for depth values) (default: 0.1)
            far (float): Far clipping plane (for depth values) (default: 100)
            light_intensity_ambient (float): Intensity of ambient light (in the
                range :math:`\left[ 0, 1 \right]`) (default: 0.5).
            light_intensity_directional (float): Intensity of directional light
                (in the range :math:`\left[ 0, 1 \right]`) (default: 0.5).
            light_color_ambient (torch.Tensor): Color of ambient light
                (default: :math:`\left[ 0, 0, 0 \right]`)
            light_color_directional (torch.Tensor): Color of directional light
                (default: :math:`\left[ 0, 0, 0 \right]`)
            light_direction (torch.Tensor): Light direction, for directional
                light (default: :math:`\left[ 0, 1, 0 \right]`)
            device (torch.Tensor): Device on which all tensors are stored.
                NOTE: Although the default device is set to 'cpu', at the moment,
                rendering will work only if the device is CUDA enabled.
                Eg. 'cuda:0'.

        """

        super(SoftRenderer, self).__init__()

        # Size of the image to be generated.
        self.image_size = image_size
        # Whether or not to enable anti-aliasing
        # If enabled, we render an image that is twice as large as the required
        # size, and then downsample it.
        self.anti_aliasing = anti_aliasing
        # Background color of the rendered image.
        self.bg_color = bg_color
        # Whether or not to fill in color to the back faces of each triangle.
        # Usually helps, especially when some of the triangles in the mesh
        # have improper orientation specifications.
        self.fill_back = fill_back

        # Device on which tensors of the class reside. At present, this function
        # only works when the device is CUDA enabled, such as a GPU.
        self.device = device

        # camera_mode specifies how the scene is to be set up.
        self.camera_mode = camera_mode
        # If the mode is 'projection', use the input camera intrinsics and
        # extrinsics.
        if self.camera_mode == 'projection':
            self.K = K
            self.rmat = rmat
            self.tvec = tvec
        # If the mode is 'look' or 'look_at', use the viewing angle to determine
        # perspective distortion and camera position and orientation.
        elif self.camera_mode in ['look', 'look_at']:
            # Whether or not to perform perspective distortion.
            self.perspective_distort = perspective_distort
            # Set the viewing angle
            self.viewing_angle = viewing_angle
            # Set the position of the eye
            self.eye = torch.FloatTensor([0, 0, 
                                         -(1. / torch.tan(torch.Tensor([math.pi]) *
                                           self.viewing_angle / 180) + 1)]).to(self.device)
            # Direction in which the camera's optical axis is facing
            self.camera_direction = torch.FloatTensor([0, 0, 1]).to(self.device)

        # Near and far clipping planes.
        self.near = near
        self.far = far

        # Soft renderer parameters (control the extent of influence of pixels over mesh)
        self.sigma_val = sigma_val
        self.dist_func = dist_func
        self.dist_eps = dist_eps
        self.gamma_val = gamma_val
        self.aggr_func_rgb = aggr_func_rgb
        self.aggr_func_alpha = aggr_func_alpha
        self.texture_type = texture_type

        # Ambient and directional lighting parameters.
        self.light_intensity_ambient = light_intensity_ambient
        self.light_intensity_directional = light_intensity_directional
        self.light_color_ambient = light_color_ambient.to(device)
        self.light_color_directional = light_color_directional.to(device)
        self.light_direction = light_direction.to(device)

        # Set rasterizer epsilon value.
        self.rasterizer_eps = 1e-3

        # Initialize rasterizer here.
        self.soft_rasterizer = SoftRasterizer(self.image_size, self.bg_color,
                                              self.near, self.far,
                                              self.anti_aliasing,
                                              self.fill_back,
                                              self.rasterizer_eps,
                                              self.sigma_val,
                                              self.dist_func,
                                              self.dist_eps,
                                              self.gamma_val,
                                              self.aggr_func_rgb,
                                              self.aggr_func_alpha,
                                              self.texture_type)

    def forward(self, vertices: torch.Tensor, faces: torch.Tensor, textures: Optional[torch.Tensor] = None,
                mode: Optional[torch.Tensor] = None, K: Optional[torch.Tensor] = None,
                rmat: Optional[torch.Tensor] = None, tvec: Optional[torch.Tensor] = None):

        return self.render(vertices, faces, textures, mode, K, rmat, tvec)

    def render(self, vertices: torch.Tensor, faces: torch.Tensor, textures: Optional[torch.Tensor] = None,
               mode: Optional[torch.Tensor] = None, K: Optional[torch.Tensor] = None,
               rmat=None, tvec=None):
        r"""Renders the RGB, depth, and alpha channels.

        Args:
            vertices (torch.Tensor): Vertices of the mesh (shape: :math:`B
                \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
            faces (torch.Tensor): Faces of the mesh (shape: :math:`B \times
                F \times 3`), where :math:`B` is the batchsize, and :math:`F`
                is the number of faces in the mesh.
            textures (torch.Tensor): Mesh texture (shape: :math:`B \times F
                \times 4 \times 4 \times 4 \times 3`)
            mode (str): Renderer mode (choices: 'rgb', 'silhouette',
                'depth', None) (default: None). If the mode is None, the rgb,
                depth, and alpha channels are all rendered. In the rgb mode,
                only the rgb image channels are rendered. In the silhouette
                mode, only a silhouette image is rendered. In the depth mode,
                only a depth image is rendered.
            K (torch.Tensor): Camera intrinsics (default: None) (shape:
                :math:`B \times 4 \times 4` or :math:`4 \times 4`)
            rmat (torch.Tensor): Rotation matrix (default: None) (shape:
                :math:`B \times 4 \times 4` or :math:`4 \times 4`)
            tvec (torch.Tensor): Translation vector (default: None)
                (shape: :math:`B \times 3` or :math:`3`)

        Returns:
            (torch.Tensor): rendered RGB image channels
            (torch.Tensor): rendered depth channel
            (torch.Tensor): rendered alpha channel

            Each of the channels is of shape
            `self.image_size` x `self.image_size`.

        """

        # Fill the back faces of each triangle, if needed
        if self.fill_back:
            faces = torch.cat((faces, faces[:, :, list(reversed(range(faces.shape[-1])))]), dim=1)
            textures = torch.cat((textures, textures), dim=1)

        # Lighting (not needed when we are rendering only depth/silhouette
        # images)
        if mode not in ['depth', 'silhouette']:
            textures = self.lighting(vertices, faces, textures)

        # Transform vertices to the camera frame
        vertices = self.transform_to_camera_frame(vertices)

        # Project the vertices from the camera coordinate frame to the image.
        vertices = self.project_to_image(vertices)

        # Rasterization
        out = soft_rasterize(vertices, textures)

        rgb = out[:, :3, :, :]
        depth = out[:, 3, :, :]
        if out.shape[0] == 1:
            depth = depth.unsqueeze(1)
        # Creating a 'dummy' alpha variable for a potential future feature.
        alpha = None

        return rgb, depth, alpha

    def lighting(self, vertices: torch.Tensor, faces: torch.Tensor, textures: torch.Tensor):
        r"""Applies ambient and directional lighting to the mesh.

        Args:
            vertices (torch.Tensor): Vertices of the mesh (shape: :math:`B
                \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
            faces (torch.Tensor): Faces of the mesh (shape: :math:`B \times
                F \times 3`), where :math:`B` is the batchsize, and :math:`F`
                is the number of faces in the mesh.
            textures (torch.Tensor): Mesh texture (shape: :math:`B \times F
                \times 4 \times 4 \times 4 \times 3`)

        """

        faces_lighting = self.vertices_to_faces(vertices, faces)
        ambient_lighting = compute_ambient_light(faces_lighting, textures,
                                                 self.light_intensity_ambient,
                                                 self.light_color_ambient)
        directional_lighting = compute_directional_light(faces_lighting, textures,
                                                         self.light_intensity_directional,
                                                         self.light_color_directional)
        # Squeeze dimensions with indices 2 and 3 (NMR uses a 6 dimensional
        # code, hence the lighting functions append these additional dims
        # which are unused in softras).
        ambient_lighting = ambient_lighting.squeeze(2).squeeze(2)
        directional_lighting = directional_lighting.squeeze(2).squeeze(2)

        return ambient_lighting * textures + directional_lighting * textures

    def shading(self):
        r"""Does nothing. Placeholder for shading functionality. """
        pass

    def transform_to_camera_frame(self, vertices: torch.Tensor):
        r"""Transforms the mesh vertices to the camera frame, based on the
        camera mode to be used.

        Args:
            vertices (torch.Tensor): Mesh vertices (shape: :math:`B \times
                V \times 3`), where `B` is the batchsize, and `V` is the
                number of mesh vertices.

        Returns:
            vertices (torch.Tensor): Transformed vertices into the camera
                coordinate frame (shape: :math:`B \times V \times 3`).

        """
        if self.camera_mode == 'look_at':
            vertices = self.look_at(vertices, self.eye)

        elif self.camera_mode == 'look':
            vertices = self.look(vertices, self.eye, self.camera_direction)

        elif self.camera_mode == 'projection':
            if K is None:
                K = self.K
            if rmat is None:
                rmat = self.rmat
            if tvec is None:
                tvec = self.tvec

        return vertices

    def project_to_image(self, vertices: torch.Tensor):
        r"""Projects the mesh vertices from the camera coordinate frame down
        to the image.

        Args:
            vertices (torch.Tensor): Mesh vertices (shape: :math:`B \times
                V \times 3`), where `B` is the batchsize, and `V` is the
                number of mesh vertices.

        Returns:
            vertices (torch.Tensor): Projected image coordinates (u, v) for
                each vertex, with an appended depth channel. (shape:
                :math:`B \times V \times 3`), where :math:`B` is the
                batchsize and :math:`V` is the number of vertices.

        """

        # TODO: Replace all of these by perspective_projection. Use different
        # rmat, tvec combinations, based on the mode, but use a consistent
        # projection function across all modes. Helps avoid redundancy.
        if self.camera_mode == 'look_at':
            vertices = self.perspective_distortion(vertices,
                                                   angle=self.viewing_angle)

        elif self.camera_mode == 'look':
            vertices = self.perspective_distortion(vertices,
                                                   angle=self.viewing_angle)

        elif self.camera_mode == 'projection':
            vertices = self.perspective_projection(vertices, K, rmat, tvec)

        return vertices

    def rasterize(self, vertices: torch.Tensor, faces: torch.Tensor, textures: torch.Tensor):
        r"""Performs rasterization, i.e., conversion of triangles to pixels.

        Args:
            vertices (torch.Tensor): Vertices of the mesh (shape: :math:`B
                \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
            faces (torch.Tensor): Faces of the mesh (shape: :math:`B \times
                F \times 3`), where :math:`B` is the batchsize, and :math:`F`
                is the number of faces in the mesh.
            textures (torch.Tensor): Mesh texture (shape: :math:`B \times F
                \times 2 \times 3`)

        """

        face_vertices = self.vertices_to_faces(vertices, faces)
        # face_textures = self.vertices_to_faces(textures, faces)
        images = soft_rasterize_cuda(face_vertices, textures, self.image_size,
                                     self.bg_color, self.near, self.far,
                                     self.fill_back, self.rasterizer_eps, self.sigma_val,
                                     self.dist_func, self.dist_eps, self.gamma_val,
                                     self.aggr_func_rgb, self.aggr_func_alpha,
                                     self.texture_type)
        if self.anti_aliasing:
            images = torch.nn.functional.avg_pool2d(images, kernel_size=2,
                                                    stride=2)
        return images

    def look_at(self, vertices: torch.Tensor, eye: torch.Tensor,
                at: Optional[torch.Tensor] = torch.FloatTensor([0, 0, 0]),
                up: Optional[torch.Tensor] = torch.FloatTensor([0, 1, 0])):
        r"""Camera "looks at" an object whose center is at the tensor represented
        by "at". And "up" is the upwards direction.

        Args:
            vertices (torch.Tensor): Vertices of the mesh (shape: :math:`B
                \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
            eye (torch.Tensor): Location of the eye (camera) (shape: :math:`3`).
            at (torch.Tensor): Location of the object to look at (shape: :math:`3`).
            up (torch.Tensor): "Up" direction for the camera (shape: :math:`3`)

        Returns:
            vertices (torch.Tensor): Input vertices transformed to the camera coordinate
                frame (shape: :math:`B \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.

        """

        import torch.nn.functional as F

        device = vertices.device
        eye = eye.to(device)
        at = at.to(device)
        up = up.to(device)

        batchsize = vertices.shape[0]

        if eye.dim() == 1:
            eye = eye[None, :].repeat(batchsize, 1)
        if at.dim() == 1:
            at = at[None, :].repeat(batchsize, 1)
        if up.dim() == 1:
            up = up[None, :].repeat(batchsize, 1)

        # Create new axes
        # eps is chosen as 1e-5 because that's what the authors use
        # in their (Chainer) implementation
        z_axis = F.normalize(at - eye, eps=1e-5)
        x_axis = F.normalize(torch.cross(up, z_axis), eps=1e-5)
        y_axis = F.normalize(torch.cross(z_axis, x_axis), eps=1e-5)

        # Create rotation matrices
        R = torch.cat((x_axis[:, None, :], y_axis[:, None, :],
                       z_axis[:, None, :]), dim=1)

        # Apply
        # [B, V, 3] -> [B, V, 3] -> [B, V, 3]
        if vertices.shape != eye.shape:
            eye = eye[:, None, :]
        vertices = vertices - eye
        vertices = torch.matmul(vertices, R.transpose(1, 2))

        return vertices

    def look(self, vertices, eye, direction=torch.FloatTensor([0, 1, 0]),
             up=None):
        r"""Apply the "look" transformation to the vertices.

        Returns:
            vertices (torch.Tensor): Input vertices transformed to the camera coordinate
                frame (shape: :math:`B \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
        """

        import torch.nn.functional as F

        device = vertices.device
        direction = direction.to(device)
        if up is None:
            up = torch.FloatTensor([0, 1, 0], device=device)

        if eye.dim() == 1:
            eye = eye[None, :]
        if direction.dim() == 1:
            direction = direction[None, :]
        if up.dim() == 1:
            up = up[None, :]

        # Create new axes
        z_axis = F.normalize(direction, eps=1e-5)
        x_axis = F.normalize(torch.cross(up, z_axis), eps=1e-5)
        y_axis = F.normalize(torch.cross(z_axis, x_axis), eps=1e-5)

        # Create rotation matrix (B x 3 x 3)
        R = torch.cat((x_axis[:, None, :], y_axis[:, None, :],
                       z_axis[:, None, :]), dim=1)

        # Apply
        if vertices.shape != eye.shape:
            eye = eye[:, None, :]
        vertices = vertices - eye
        vertices = torch.matmul(vertices, R.transpose(1, 2))

        return vertices

    def perspective_distortion(self, vertices, angle=30.):
        r"""Compute perspective distortion from a given viewing angle.

        Returns:
            vertices (torch.Tensor): Input vertices transformed to the camera coordinate
                frame (shape: :math:`B \times V \times 3`), where :math:`B` is the batchsize,
                and :math:`V` is the number of vertices in the mesh.
        """
        device = vertices.device
        angle = torch.FloatTensor([angle * 180 / torch.FloatTensor([math.pi])]).to(device)
        width = torch.tan(angle)
        width = width[:, None]
        z = vertices[:, :, 2]
        x = vertices[:, :, 0] / (z * width)
        y = vertices[:, :, 1] / (z * width)
        vertices = torch.stack((x, y, z), dim=2)
        return vertices

    def vertices_to_faces(self, vertices, faces):
        r"""
        vertices (torch.Tensor): shape: math:`B \times V \times 3`
        faces (torch.Tensor): shape: math:`B \times F \times 3`
        """
        B = vertices.shape[0]
        V = vertices.shape[1]
        device = vertices.device
        faces = faces + (torch.arange(B, device=device) * V)[:, None, None]
        vertices = vertices.reshape(B * V, 3)
        return vertices[faces]

    def textures_to_faces(self, textures, faces):
        r"""
        textures (torch.Tensor): shape: math:`B \times F \times 2 \times 3`
        faces (torch.Tensor): shape: math:`B \times F \times 3`
        """
        B = vertices.shape[0]
        V = vertices.shape[1]
        # print(vertices.dim(), faces.dim())
        # print(vertices.shape, faces.shape)
        # print(vertices.shape, faces.shape)
        device = vertices.device
        faces = faces + (torch.arange(B, device=device) * V)[:, None, None]
        vertices = vertices.reshape(B * V, 3)
        return vertices[faces]
