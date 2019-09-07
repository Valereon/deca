from deca.vfs_base import VfsBase, VfsNode
from deca.ff_types import *
from deca.ff_sarc import FileSarc, EntrySarc
from deca.ff_avtx import image_import
from deca.errors import *
from deca.file import ArchiveFile
import os
import shutil
import re
import numpy as np
from pprint import pprint, pformat
from copy import deepcopy


class Builder:
    def __init__(self):
        pass

    def build_node(self, dst_path: str, vnode: VfsNode, vfs: VfsBase, src_map):
        if vnode.ftype == FTYPE_SARC:
            print('BUILD SARC {}'.format(vnode.vpath))

            # parse existing file
            sarc_old = FileSarc()
            f = vfs.file_obj_from(vnode)
            sarc_old.deserialize(f)

            sarc_new = deepcopy(sarc_old)

            data_write_pos = sarc_old.dir_block_len + 16  # 16 is the basic header length 4,b'SARC', 3, dir_block_len
            src_files = [None] * len(sarc_old.entries)
            for i in range(len(sarc_old.entries)):
                entry_old: EntrySarc = sarc_old.entries[i]
                entry_new: EntrySarc = sarc_new.entries[i]
                vpath = entry_old.vpath

                make_symlink = False
                if vpath in src_map:
                    src_files[i] = src_map[vpath]
                    sz = os.stat(src_files[i]).st_size
                    make_symlink = True
                else:
                    sz = entry_old.length

                if entry_old.offset == 0 or make_symlink:
                    entry_new.offset = 0
                    entry_new.length = sz
                else:
                    # IMPORTANT SARCS apparently don't want data to cross 32MB boundary (maybe small boundary?)
                    max_block_size = 32 * 1024 * 1024
                    if sz > max_block_size:
                        raise NotImplementedError('Excessive file size: {}'.format(vpath))

                    block_pos_diff = \
                        np.floor((data_write_pos + sz) / max_block_size) - np.floor(data_write_pos / max_block_size)

                    if block_pos_diff > 0:
                        # boundary crossed
                        data_write_pos = ((data_write_pos + max_block_size - 1) // max_block_size) * max_block_size

                    entry_new.offset = data_write_pos
                    entry_new.length = sz
                    data_write_pos = data_write_pos + sz
                    align = 4
                    data_write_pos = ((data_write_pos + align - 1) // align) * align

            # extract existing file
            fn_dst = os.path.join(dst_path, vnode.vpath.decode('utf-8'))
            # fn = vfs.extract_node(vnode, dst_path, do_sha1sum=False, allow_overwrite=True)

            # modify extracted existing file by overwriting offset to file entry to zero, telling the engine that it is
            # a symbolic link, and should be loaded elsewhere, preferably directly
            pt, fn = os.path.split(fn_dst)
            os.makedirs(pt, exist_ok=True)
            with ArchiveFile(open(fn_dst, 'wb')) as fso:
                with ArchiveFile(vfs.file_obj_from(vnode, 'rb')) as fsi:
                    buf = fsi.read(sarc_old.dir_block_len + 16)
                    fso.write(buf)

                    for i in range(len(sarc_old.entries)):
                        entry_old: EntrySarc = sarc_old.entries[i]
                        entry_new: EntrySarc = sarc_new.entries[i]

                        fso.seek(entry_new.META_entry_offset_ptr)
                        fso.write_u32(entry_new.offset)
                        fso.seek(entry_new.META_entry_size_ptr)
                        fso.write_u32(entry_new.length)

                        buf = None

                        if entry_new.offset == 0:
                            print('  SYMLINK {}'.format(entry_old.vpath))
                        elif src_files[i] is not None:
                            print('  INSERTING {} src file to new file'.format(entry_old.vpath))
                            with open(src_files[i], 'rb') as f:
                                buf = f.read(entry_new.length)
                        else:
                            print('  COPYING {} from old file to new file'.format(entry_old.vpath))
                            fsi.seek(entry_old.offset)
                            buf = fsi.read(entry_old.length)

                        if buf is not None:
                            fso.seek(entry_new.offset)
                            fso.write(buf)

            return fn_dst
        else:
            raise EDecaBuildError('Cannot build {} : {}'.format(vnode.ftype, vnode.vpath))

    def build_dir(self, vfs: VfsBase, src_path: str, dst_path: str):
        # find all changed src files
        src_files = []

        if isinstance(src_path, bytes):
            src_path = src_path.decode('utf-8')
        if isinstance(dst_path, bytes):
            dst_path = dst_path.decode('utf-8')

        wl = [src_path]
        while len(wl) > 0:
            cpath = wl.pop(0)
            print('Process: {}'.format(cpath))
            if os.path.isdir(cpath):
                cdir = os.listdir(cpath)
                for entry in cdir:
                    wl.append(os.path.join(cpath, entry))
            elif os.path.isfile(cpath):
                file, ext = os.path.splitext(cpath)
                if ext == '.deca_sha1sum':
                    pass
                else:
                    vpath = cpath[len(src_path):].encode('ascii')
                    vpath = vpath.replace(b'\\', b'/')
                    src_files.append([vpath, cpath])

        # copy src modified files to build directory
        vpaths_completed = {}
        pack_list = []
        for file in src_files:
            vpath: bytes = file[0]
            fpath: str = file[1]
            # print('vpath: {}, src: {}'.format(vpath, fpath))
            dst = os.path.join(dst_path, vpath.decode('utf-8'))
            dst_dir = os.path.dirname(dst)
            os.makedirs(dst_dir, exist_ok=True)

            if fpath.find('REFERENCE_ONLY') >= 0:
                pass  # DO NOT USE THESE FILES
            elif re.match(r'^.*\.ddsc$', fpath) or re.match(r'^.*\.hmddsc$', fpath) or re.match(r'^.*\.atx?$', fpath):
                pass  # DO NOT USE THESE FILES image builder should use .ddsc.dds
            elif fpath.endswith('.ddsc.dds'):
                vpath = vpath[0:-4]
                vnode = vfs.map_vpath_to_vfsnodes[vpath][0]

                # make ddsc.dds into ddsc and avtxs
                compiled_files = image_import(vfs, vnode, fpath, dst_path)
                for cfile in compiled_files:
                    vpath = cfile[0]
                    dst = cfile[1]
                    pack_list.append([vpath, dst])
                    vpaths_completed[vpath] = dst
            else:
                shutil.copy2(fpath, dst)
                pack_list.append([vpath, dst])
                vpaths_completed[vpath] = dst

        # calculate dependencies
        depends = {}
        while len(pack_list) > 0:
            file = pack_list.pop(0)
            # print(file)
            vpath = file[0]
            dst = file[1]

            if vpath not in vfs.map_vpath_to_vfsnodes:
                print('TODO: WARNING: FILE {} NOT HANDLED'.format(vpath))
            else:
                vnodes = vfs.map_vpath_to_vfsnodes[vpath]
                for vnode in vnodes:
                    vnode: VfsNode = vnode
                    pid = vnode.pid
                    if pid is not None:
                        pnode = vfs.table_vfsnode[pid]

                        if pnode.ftype == FTYPE_GDCBODY:
                            # handle case of gdcc files
                            pid = pnode.pid
                            pnode = vfs.table_vfsnode[pid]

                        if pnode.ftype != FTYPE_ARC and pnode.ftype != FTYPE_TAB:
                            if pnode.vpath is None:
                                raise EDecaBuildError('MISSING VPATH FOR uid:{} hash:{:08X}, when packing {}'.format(
                                    pnode.uid, pnode.vhash, vnode.vpath))
                            else:
                                depends[pnode.vpath] = depends.get(pnode.vpath, set()).union({vnode.vpath})
                                pack_list.append([pnode.vpath, os.path.join(dst_path, pnode.vpath.decode('utf-8'))])

        # pprint(depends, width=128)

        any_changes = True
        vpaths_todo = set()

        while any_changes:
            any_changes = False
            vpaths_todo = set()

            for dep, srcs in depends.items():
                if dep in vpaths_completed:
                    pass  # this file is done
                else:
                    all_src_ready = True
                    for src in srcs:
                        if src not in vpaths_completed:
                            all_src_ready = False
                            break
                    if all_src_ready:
                        vnodes = vfs.map_vpath_to_vfsnodes[dep]
                        dst = self.build_node(dst_path, vnodes[0], vfs, vpaths_completed)
                        any_changes = True
                        vpaths_completed[dep] = dst
                    else:
                        vpaths_todo.add(dep)

        if len(vpaths_todo):
            print('BUILD FAILED: Not Completed:')
            pprint(vpaths_todo)
            raise EDecaBuildError('BUILD FAILED\n' + pformat(vpaths_todo))
        else:
            print('BUILD SUCCESS:')
            for k, v in vpaths_completed.items():
                print(v)

    def build_src(self, vfs: VfsBase, src_file: str, dst_path: str):
        # TODO Eventually process a simple script to update files based on relative addressing to handle other mods and
        #  patches
        pass
