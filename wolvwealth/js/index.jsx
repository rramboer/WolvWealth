import React, { useEffect, useState } from "react";
import Data from "./components/data";
import Help from "./components/help";
import Contact from "./components/contact";

function Index() {
    const [currentPage, setCurrentPage] = useState(0); // 0 = dashboard, 1 = contact, 2 = help
    const [isOpaque, setIsOpaque] = useState(false);

    // let name = "Geoff";

    const handleSidebarClick = (e) => {
        setCurrentPage(e.target.value);
    }

    return (
        <div className="bg-gray-dark">
            <div className="sticky top-0 w-screen">
                <nav>
                    <div className="max-w-screen-xl flex flex-wrap items-center justify-between mx-auto p-4 ">
                        <a href="/" className="flex items-center space-x-3 rtl:space-x-reverse">
                            <img src="/static/img/logo.png" className="h-8" alt="WolvWealth Logo" />
                            <span className="self-center text-2xl font-semibold whitespace-nowrap dark:text-white">WolvWealth</span>
                        </a>
                        <button data-collapse-toggle="navbar-default" type="button" className="inline-flex items-center p-2 w-10 h-10 justify-center text-sm text-gray-500 rounded-lg md:hidden hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-gray-200 dark:text-gray-400 dark:hover:bg-gray-700 dark:focus:ring-gray-600" aria-controls="navbar-default" aria-expanded="false">
                            <span className="sr-only">Open main menu</span>
                            <svg className="w-5 h-5" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 17 14">
                                <path stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M1 1h15M1 7h15M1 13h15"/>
                            </svg>
                        </button>
                        <div className="hidden w-full md:block md:w-auto" id="navbar-default">
                            <ul className="font-medium flex flex-col p-4 md:p-0 mt-4 border border-gray-100 rounded-lg bg-gray-50 md:flex-row md:space-x-8 rtl:space-x-reverse md:mt-0 md:border-0">
                            <li>
                                <button value={0} onClick={handleSidebarClick} className="block py-2 px-3 text-white rounded hover:bg-gray-100 md:hover:bg-transparent md:border-0 md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Dashboard</button>
                            </li>
                            <li>
                                <button value={2} onClick={handleSidebarClick} className="block py-2 px-3 text-white rounded hover:bg-gray-100 md:hover:bg-transparent md:border-0 md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Help</button>
                            </li>
                            <li>
                                <button value={1} onClick={handleSidebarClick} className="block py-2 px-3 text-white rounded hover:bg-gray-100 md:hover:bg-transparent md:border-0 md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Contact</button>
                            </li>
                            <li>
                                <a href="/accounts/logout/" className="block py-2 px-3 text-white rounded hover:bg-gray-100 md:hover:bg-transparent md:border-0 md:hover:text-blue-700 md:p-0 dark:text-white md:dark:hover:text-blue-500 dark:hover:bg-gray-700 dark:hover:text-white md:dark:hover:bg-transparent">Logout</a>
                            </li>
                            </ul>
                        </div>
                    </div>
                </nav>
            </div>
            <div id="content" className="mx-16" style={{width:"-webkit-fill-available"}}>
                <div>
                    {(currentPage == 0 ? <Data name={name} className="flex"/> : 
                    currentPage == 1 ? (<Contact/>) : (<Help/>))}
                </div>
            </div>
        </div>
    )
}

export default Index;
