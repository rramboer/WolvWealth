import React, { useState } from "react";
import Data from "./components/data";
import Help from "./components/help";
import Contact from "./components/contact";

function Index() {
    const [currentPage, setCurrentPage] = useState(0); // 0 = dashboard, 1 = contact, 2 = help

    let name = "Geoff";

    const handleSidebarClick = (e) => {
        setCurrentPage(e.target.value);
    }

    return (
        <div className="flex flex-row h-[100%]">
            <div id="sidebar" className="px-10 py-10 bg-gray-dark rounded-r-3xl">
                <h1 className="pb-5 text-4xl font-bold">
                    WolvWealth
                </h1>
                <div className="flex flex-col w-[100%] float-left text-left">
                    <button value={0} onClick={handleSidebarClick} className="text-2xl mt-2 p-3 text-left rounded-2xl" style={{background:(currentPage==0 ? "grey" : "none")}}>Dashboard</button>
                    <button value={1} onClick={handleSidebarClick} className="text-2xl mt-2 p-3 text-left rounded-2xl" style={{background:(currentPage==1 ? "grey" : "none")}}>Contact</button>
                    <button value={2} onClick={handleSidebarClick} className="text-2xl mt-2 p-3 text-left rounded-2xl" style={{background:(currentPage==2 ? "grey" : "none")}}>Help</button>
                </div>
            </div>
            <div id="content" className="" style={{width:"-webkit-fill-available"}}>
                <div>
                    {(currentPage == 0 ? <Data name={name} className="flex"/> : 
                    currentPage == 1 ? (<Contact/>) : (<Help/>))}
                </div>
            </div>
        </div>
    )
}

export default Index;
