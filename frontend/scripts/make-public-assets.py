"""Generate original generic public-demo geometry using only Python's stdlib.

No source mesh, game imagery, texture, or third-party branding is read.
Material identifiers retain the existing renderer interface.
"""
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]

def main():
    binary=bytearray(); views=[]; accessors=[]; meshes=[]; nodes=[]; materials=[]
    def attribute(values,kind,bounds=None):
        flat=[x for row in values for x in row]; offset=len(binary)
        binary.extend(struct.pack('<'+'f'*len(flat),*flat))
        views.append({'buffer':0,'byteOffset':offset,'byteLength':len(flat)*4,'target':34962})
        obj={'bufferView':len(views)-1,'componentType':5126,'count':len(values),'type':kind}
        if bounds:obj.update(min=bounds[0],max=bounds[1])
        accessors.append(obj);return len(accessors)-1
    def box(name,center,size,color):
        lo=[c-s/2 for c,s in zip(center,size)]; hi=[c+s/2 for c,s in zip(center,size)]
        corners=[(x,y,z) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
        faces=[([0,1,3,2],[-1,0,0]),([4,6,7,5],[1,0,0]),([0,4,5,1],[0,-1,0]),([2,3,7,6],[0,1,0]),([0,2,6,4],[0,0,-1]),([1,5,7,3],[0,0,1])]
        positions=[]; normals=[]
        for indices,normal in faces:
            for j in (0,1,2,0,2,3):positions.append(corners[indices[j]]);normals.append(normal)
        pos=attribute(positions,'VEC3',(lo,hi));norm=attribute(normals,'VEC3')
        materials.append({'name':name,'pbrMetallicRoughness':{'baseColorFactor':[*color,1],'metallicFactor':0.05,'roughnessFactor':0.65}})
        meshes.append({'primitives':[{'attributes':{'POSITION':pos,'NORMAL':norm},'material':len(materials)-1}]})
        nodes.append({'name':'Generic_'+name,'mesh':len(meshes)-1})
    # A plain rectangular archive card with a label and accent strip.
    box('Optical_Diffuser',(0,1.85,0),(4.8,3.7,.34),(.86,.82,.74))
    box('Ivory_Edges',(0,1.85,-.2),(5.0,3.7,.06),(.93,.9,.84))
    box('Printed_Label',(0,2.08,.2),(4.15,1.65,.04),(.94,.92,.86))
    box('Index_Inlay',(0,.36,.2),(4.1,.12,.04),(.67,.53,.38))
    gltf={'asset':{'version':'2.0','generator':'Learning Radar original generic archive card'},'scene':0,'scenes':[{'nodes':list(range(len(nodes)))}],'nodes':nodes,'meshes':meshes,'materials':materials,'buffers':[{'byteLength':len(binary)}],'bufferViews':views,'accessors':accessors}
    encoded=json.dumps(gltf,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
    binary+=b'\0'*((-len(binary))%4)
    data=struct.pack('<4sII',b'glTF',2,12+8+len(encoded)+8+len(binary))+struct.pack('<I4s',len(encoded),b'JSON')+encoded+struct.pack('<I4s',len(binary),b'BIN\0')+binary
    out=ROOT/'public/assets/archive-cassette.glb';out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data)
    print('Generated original generic archive card:',len(data),'bytes')

if __name__=='__main__':main()
