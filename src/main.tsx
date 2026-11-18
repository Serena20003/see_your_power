import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppPage from "./pages/AppPage";
import ModelSources from "./ModelSources";
import ProtectedRoute from "./components/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import OnboardingPage from "./pages/OnboardingPage";
import ProfilePage from "./pages/ProfilePage";
import { AuthProvider } from "./lib/supabase/AuthContext";
import "./styles.scss";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public routes */}
          <Route path="/" element={<AppPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/model-sources" element={<ModelSources />} />

          {/* Protected routes */}
          <Route element={<ProtectedRoute />}>
            <Route path="/onboarding" element={<OnboardingPage />} />
            <Route path="/profile" element={<ProfilePage />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
