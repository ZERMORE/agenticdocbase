import React,{createContext,useContext} from 'react';
export const WorkspaceContext=createContext<any>(null);
export function useWorkspace(){return useContext(WorkspaceContext);}
