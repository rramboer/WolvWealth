import React from "react";
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import Optimization from "./pages/optimizer";

const App = () => {
    return (
        <Router>
            <Routes>
                <Route path="/optimizer/" element={<Optimization />} />
            </Routes>
        </Router>
    );
};

export default App;
