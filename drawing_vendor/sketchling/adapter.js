import {Stroke} from './src/core/stroke.ts';
import {pathFromPoints} from './src/core/geometry.ts';
import {roughOptionsFor} from './src/render/style.ts';
import rough from 'roughjs';
window.kadroRender = async function(data) {
  const result=document.getElementById('result');
  try {
    const {scene,drawing,colours,width,height}=data;
    const ns='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(ns,'svg');svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const bg=document.createElementNS(ns,'rect');bg.setAttribute('width',width);bg.setAttribute('height',height);bg.setAttribute('fill','#faf7f0');svg.appendChild(bg);
    document.getElementById('stage').appendChild(svg);const renderer=rough.svg(svg);
    const symbols=new Map(drawing.symbols.map(s=>[s.id,s]));let n=0;
    for(const object of scene.objects){
      const shapes=object.symbol?symbols.get(object.symbol).shapes:object.shapes;
      for(const shape of shapes){
        const points=shape.points.map(([x,y])=>[(object.x+x*object.w)*width,(object.y+y*object.h)*height]);
        const style={color:colours[shape.stroke],weight:Math.max(.7,shape.width*height),looseness:.16,smooth:shape.smooth};
        if(shape.fill!==null&&shape.closed)style.fill={color:colours[shape.fill],style:'solid'};
        const node=new Stroke(points,style,shape.closed).withSeed(drawing.seed+n);
        svg.appendChild(renderer.path(pathFromPoints(node.points,node.closed,node.style.smooth),roughOptionsFor(node.style,node.seed,node.closed,'ink')));n++;
      }
    }
    const source=new XMLSerializer().serializeToString(svg);
    const image=new Image();image.width=width;image.height=height;
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error('SVG rasterization failed'));image.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(source);});
    const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
    canvas.getContext('2d').drawImage(image,0,0);
    result.textContent=canvas.toDataURL('image/png').split(',')[1];
  }catch(error){result.textContent='ERROR: '+String(error);}
};
