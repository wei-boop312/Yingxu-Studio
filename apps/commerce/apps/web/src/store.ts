import {create} from 'zustand';
import type {Region} from './api';
type Editor={regions:Region[],zoom:number,mode:'draw'|'pan',setRegions:(r:Region[])=>void,setZoom:(z:number)=>void,setMode:(m:'draw'|'pan')=>void,reset:()=>void};
export const useEditor=create<Editor>(set=>({regions:[],zoom:1,mode:'draw',setRegions:regions=>set({regions}),setZoom:zoom=>set({zoom}),setMode:mode=>set({mode}),reset:()=>set({regions:[],zoom:1,mode:'draw'})}));
