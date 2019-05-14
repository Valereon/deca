import os
import io
import time
import numpy as np
from PIL import Image
from deca.file import ArchiveFile
from deca.errors import DecaFileExists
from deca.ff_types import *
import deca.dxgi


class DecaImage:
    def __init__(
            self, sx=None, sy=None, depth_cnt=None, depth_idx=None, pixel_format=None,
            itype=None, data=None, raw_data=None, filename=None):
        self.size_x = sx
        self.size_y = sy
        self.depth_cnt = depth_cnt
        self.depth_idx = depth_idx
        self.pixel_format = pixel_format
        self.itype = itype
        self.data = data
        self.raw_data = raw_data
        self.filename = filename

    def pil_image(self):
        return Image.fromarray(self.data)


class Ddsc:
    def __init__(self):
        self.header_buffer = None
        self.magic = None
        self.version = None
        self.unknown = None
        self.dim = None
        self.pixel_format = None
        self.nx0 = None
        self.ny0 = None
        self.depth = None
        self.flags = None
        self.full_mip_count = None
        self.mip_count = None
        self.mips = None

    def load_bmp(self, f):
        im = Image.open(f)
        im.convert('RGBA')
        self.mips = [DecaImage(sx=im.size[0], sy=im.size[1], itype='bmp', data=np.array(im))]

    def load_dds(self, f):
        im = Image.open(f)
        im.convert('RGBA')
        self.mips = [DecaImage(sx=im.size[0], sy=im.size[1], itype='dds', data=np.array(im))]

    def load_ddsc(self, f, filename=None, save_raw_data=False):
        header = f.read(128)
        self.header_buffer = header

        fh = ArchiveFile(io.BytesIO(header))

        self.unknown = []
        self.magic = fh.read_u32()
        self.version = fh.read_u16()
        self.unknown.append(fh.read_u8())
        self.dim = fh.read_u8()
        self.pixel_format = fh.read_u32()
        self.nx0 = fh.read_u16()
        self.ny0 = fh.read_u16()
        self.depth = fh.read_u16()
        self.flags = fh.read_u16()
        self.full_mip_count = fh.read_u8()
        self.mip_count = fh.read_u8()
        self.unknown.append(fh.read_u16())
        while fh.tell() < 128:
            self.unknown.append(fh.read_u32())

        print('Compression Format: {}'.format(self.pixel_format))

        nx = self.nx0
        ny = self.ny0
        self.mips = []
        for i in range(self.full_mip_count):
            for j in range(self.depth):
                self.mips.append(DecaImage(
                    sx=nx, sy=ny, depth_cnt=self.depth, depth_idx=j, pixel_format=self.pixel_format, itype='missing'))

            nx = nx // 2
            ny = ny // 2

        for midx in range((self.full_mip_count - self.mip_count) * self.depth, self.full_mip_count * self.depth):
            mip = self.mips[midx]
            pixel_format = mip.pixel_format
            nx = mip.size_x
            ny = mip.size_y
            if nx == 0 or ny == 0:
                break
            nxm = max(4, nx)
            nym = max(4, ny)
            raw_size = deca.dxgi.raw_data_size(pixel_format, nx, ny)
            # print('Loading Data: {}'.format(raw_size))
            raw_data = f.read(raw_size)
            if len(raw_data) < raw_size:
                raise Exception('Ddsc::load_ddsc: Not Enough Data')

            inp = np.zeros((nym, nxm, 4), dtype=np.uint8)
            # print('Process Data: {}'.format(mip))
            # t0 = time.time()
            deca.dxgi.process_image(inp, raw_data, nx, ny, pixel_format)
            # inp = inp[0:ny, 0:nx, :]  # TODO Qt cannot display 2x2 for some reason
            if ny < nym or nx < nxm:
                inp[ny:, :, :] = 0
                inp[:, nx:, :] = 0
            # t1 = time.time()
            # print('Execute time: {} s'.format(t1 - t0))
            mip.itype = 'ddsc'
            mip.data = inp
            mip.filename = filename

            if save_raw_data:
                mip.raw_data = raw_data

    def load_atx(self, f, filename=None, save_raw_data=False):
        first_loaded = 0
        while first_loaded < len(self.mips):
            if self.mips[first_loaded].data is None:
                first_loaded = first_loaded + 1
            else:
                break

        for midx in range(first_loaded - 1, -1, -1):
            mip = self.mips[midx]
            pixel_format = mip.pixel_format
            nx = mip.size_x
            ny = mip.size_y
            if nx == 0 or ny == 0:
                break
            nxm = max(4, nx)
            nym = max(4, ny)
            raw_size = deca.dxgi.raw_data_size(pixel_format, nx, ny)
            # print('Loading Data: {}'.format(raw_size))
            raw_data = f.read(raw_size)
            raw_data_size = len(raw_data)
            if raw_data_size == 0:
                break  # Ran out of data probably because more data is in another atx
            if raw_data_size < raw_size:
                raise Exception('Ddsc::load_atx: Not Enough Data')
            inp = np.zeros((nym, nxm, 4), dtype=np.uint8)
            # print('Process Data: {}'.format(mip))
            # t0 = time.time()
            deca.dxgi.process_image(inp, raw_data, nx, ny, pixel_format)
            # t1 = time.time()
            # print('Execute time: {} s'.format(t1 - t0))

            mip.itype = 'atx'
            mip.data = inp
            mip.filename = filename

            if save_raw_data:
                mip.raw_data = raw_data

    def load_ddsc_atx(self, files, save_raw_data=False):
        self.load_ddsc(files[0][1], filename=files[0][0], save_raw_data=save_raw_data)
        for finfo in files[1:]:
            self.load_atx(finfo[1], filename=finfo[0], save_raw_data=save_raw_data)


def image_load(vfs, vnode, save_raw_data=False):
    if vnode.ftype == FTYPE_BMP:
        f_ddsc = vfs.file_obj_from(vnode)
        ddsc = Ddsc()
        ddsc.load_bmp(f_ddsc)
    elif vnode.ftype == FTYPE_DDS:
        f_ddsc = vfs.file_obj_from(vnode)
        ddsc = Ddsc()
        ddsc.load_dds(f_ddsc)
    elif vnode.ftype in {FTYPE_AVTX, FTYPE_ATX, FTYPE_HMDDSC}:
        if vnode.vpath is None:
            f_ddsc = vfs.file_obj_from(vnode)
            ddsc = Ddsc()
            ddsc.load_ddsc(f_ddsc)
        else:
            filename = os.path.splitext(vnode.vpath)
            if len(filename[1]) == 0 and vnode.ftype == FTYPE_AVTX:
                filename_ddsc = vnode.vpath
            else:
                filename_ddsc = filename[0] + b'.ddsc'

            if filename_ddsc in vfs.map_vpath_to_vfsnodes:
                extras = [b'.hmddsc']
                for i in range(1, 16):
                    extras.append('.atx{}'.format(i).encode('ascii'))

                files = []
                files.append([
                    filename_ddsc,
                    vfs.file_obj_from(vfs.map_vpath_to_vfsnodes[filename_ddsc][0]),
                ])
                for extra in extras:
                    filename_atx = filename[0] + extra
                    if filename_atx in vfs.map_vpath_to_vfsnodes:
                        files.append([
                            filename_atx,
                            vfs.file_obj_from(vfs.map_vpath_to_vfsnodes[filename_atx][0]),
                        ])
                ddsc = Ddsc()
                ddsc.load_ddsc_atx(files, save_raw_data=save_raw_data)
    return ddsc


def image_export(vfs, node, ofile, allow_overwrite=False):
    existing_files = []
    ddsc = image_load(vfs, node, save_raw_data=True)
    if ddsc is not None:
        ofile = os.path.splitext(ofile)[0]
        ofile = ofile + '.ddsc'

        # export to reference png file
        ofile_img = ofile + '.REFERENCE_ONLY.png'
        if not allow_overwrite and os.path.isfile(ofile_img):
            existing_files.append(ofile_img)
        else:
            npimp = ddsc.mips[0].pil_image()
            npimp.save(ofile_img)

        ofile_img = ofile + '.dds'

        flags = 0
        flags = flags | 0x1         # DDSD_CAPS
        flags = flags | 0x2         # DDSD_HEIGHT
        flags = flags | 0x4         # DDSD_WIDTH
        # flags = flags | 0x8         # DDSD_PITCH
        flags = flags | 0x1000      # DDSD_PIXELFORMAT
        flags = flags | 0x20000     # DDSD_MIPMAPCOUNT
        flags = flags | 0x80000     # DDSD_LINEARSIZE

        dwCaps1 = 0x8 | 0x1000 | 0x400000
        dwCaps2 = 0
        resourceDimension = 3

        if ddsc.depth > 1:
            flags = flags | 0x800000        # DDSD_DEPTH
            dwCaps2 = dwCaps2 | 0x200000
            resourceDimension = 4

        with ArchiveFile(open(ofile_img, 'wb')) as f:
            # magic word
            f.write(b'DDS ')
            # DDS_HEADER
            f.write_u32(124)            # dwSize
            f.write_u32(flags)          # dwFlags
            f.write_u32(ddsc.ny0)       # dwHeight
            f.write_u32(ddsc.nx0)       # dwWidth
            f.write_u32(len(ddsc.mips[0].raw_data))  # dwPitchOrLinearSize
            f.write_u32(ddsc.depth)     # dwDepth
            f.write_u32(ddsc.full_mip_count)  # dwMipMapCount
            for i in range(11):
                f.write_u32(0)  # dwReserved1

            # PIXEL_FORMAT
            DDPF_FOURCC = 0x4
            f.write_u32(32)  # DWORD dwSize
            f.write_u32(DDPF_FOURCC)  # DWORD dwFlags
            f.write(b'DX10')  # DWORD dwFourCC
            f.write_u32(0)  # DWORD dwRGBBitCount
            f.write_u32(0)  # DWORD dwRBitMask
            f.write_u32(0)  # DWORD dwGBitMask
            f.write_u32(0)  # DWORD dwBBitMask
            f.write_u32(0)  # DWORD dwABitMask

            # DDS_HEADER, cont...
            f.write_u32(dwCaps1)          # dwCaps
            f.write_u32(dwCaps2)          # dwCaps2
            f.write_u32(0)          # dwCaps3
            f.write_u32(0)          # dwCaps4
            f.write_u32(0)          # dwReserved2

            # DDS_HEADER_DXT10
            f.write_u32(ddsc.pixel_format)  # DXGI_FORMAT              dxgiFormat;
            f.write_u32(resourceDimension)  # D3D10_RESOURCE_DIMENSION resourceDimension;
            f.write_u32(0)  # UINT                     miscFlag;
            f.write_u32(1)  # UINT                     arraySize;
            f.write_u32(0)  # UINT                     miscFlags2;

            for mip in ddsc.mips:
                f.write(mip.raw_data)

        # raise exception if any files could not be overwritten
        if len(existing_files) > 0:
            raise DecaFileExists(existing_files)


def image_import(vfs, node, ifile, opath):
    print('Importing Image: {}\n  input {}\n  opath {}'.format(node.vpath, ifile, opath))
    ddsc = image_load(vfs, node, save_raw_data=True)

    compiled_files = []
    if ddsc is not None:
        with open(ifile, 'rb') as file_in:
            dsc_header = file_in.read(37 * 4 - 5 * 4)  # skip dds header

            fout = None
            fout_name = None
            vpath_out = None
            for mip in ddsc.mips:
                print('{} x {} : {}'.format(mip.size_x, mip.size_y, mip.filename))

                if vpath_out != mip.filename:
                    if vpath_out is not None:
                        compiled_files.append((vpath_out, fout_name))
                        fout.close()

                    vpath_out = mip.filename
                    fout_name = os.path.join(opath, vpath_out)
                    fout = open(fout_name, 'wb')
                    if fout_name.endswith(b'.ddsc'):
                        fout.write(ddsc.header_buffer)

                buffer = file_in.read(len(mip.raw_data))
                fout.write(buffer)

            if vpath_out is not None:
                compiled_files.append((vpath_out, fout_name))
                fout.close()

    return compiled_files

'''
typedef struct {
  DWORD           dwSize;
  DWORD           dwFlags;
  DWORD           dwHeight;
  DWORD           dwWidth;
  DWORD           dwPitchOrLinearSize;
  DWORD           dwDepth;
  DWORD           dwMipMapCount;
  DWORD           dwReserved1[11];
  DDS_PIXELFORMAT ddspf;
  DWORD           dwCaps;
  DWORD           dwCaps2;
  DWORD           dwCaps3;
  DWORD           dwCaps4;
  DWORD           dwReserved2;
} DDS_HEADER;

struct DDS_PIXELFORMAT {
  DWORD dwSize;
  DWORD dwFlags;
  DWORD dwFourCC;
  DWORD dwRGBBitCount;
  DWORD dwRBitMask;
  DWORD dwGBitMask;
  DWORD dwBBitMask;
  DWORD dwABitMask;
};

typedef struct {
  DXGI_FORMAT              dxgiFormat;
  D3D10_RESOURCE_DIMENSION resourceDimension;
  UINT                     miscFlag;
  UINT                     arraySize;
  UINT                     miscFlags2;
} DDS_HEADER_DXT10;

'''
# if dump:
#     im = PIL.Image.fromarray(inp)
#     fns = os.path.split(in_file)
#     ifn = fns[1]
#     fns = fns[0].split('/')
#     # print(fns)
#     if no_header:
#         impath = image_dump + 'raw_images/{:08x}/'.format(file_sz)
#     else:
#         impath = image_dump + '{:02d}/'.format(pixel_format)
#     impath = impath + '/'.join(fns[3:]) + '/'
#     imfn = impath + ifn + '.{:04d}x{:04d}.png'.format(fl[1], fl[2])
#     # print(imfn)
#     os.makedirs(impath, exist_ok=True)
#     if not os.path.isfile(imfn):
#         im.save(imfn)
# else:
#     plt.figure()
#     plt.imshow(inp, interpolation='none')
#     plt.show()

