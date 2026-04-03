import { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { setToken } from '../utils/api';
import { useStore } from '../utils/store';
import { LOGO_SVG } from '../utils/constants';
import '../styles/login.css';

export default function Login() {
  const navigate = useNavigate();
  const { dispatch, fetchData } = useStore();
  const [email, setEmail] = useState('');
  const [pwd, setPwd] = useState('');
  const [error, setError] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [forgotMode, setForgotMode] = useState(false);
  const [forgotStep, setForgotStep] = useState(1);
  const [forgotEmail, setForgotEmail] = useState('');
  const [resetCode, setResetCode] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [forgotError, setForgotError] = useState('');
  const [forgotSuccess, setForgotSuccess] = useState('');
  const pwdRef = useRef(null);

  async function doLogin() {
    if (!email || !pwd) { setError('Veuillez remplir email et mot de passe.'); return; }
    try {
      const r = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: pwd })
      });
      const d = await r.json();
      if (d.token) {
        setToken(d.token);
        dispatch({ type: 'SET_USER', payload: d.user });
        await fetchData();
        navigate('/dashboard');
      } else {
        setError(d.error || 'Identifiants incorrects.');
        if (pwdRef.current) {
          pwdRef.current.style.borderColor = 'var(--da)';
          pwdRef.current.classList.remove('shake');
          void pwdRef.current.offsetWidth;
          pwdRef.current.classList.add('shake');
        }
      }
    } catch {
      setError('Erreur de connexion au serveur.');
    }
  }

  async function sendResetCode() {
    if (!forgotEmail) { setForgotError('Entrez votre email'); return; }
    setForgotError('');
    try {
      const r = await fetch('/api/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: forgotEmail })
      });
      const d = await r.json();
      if (d.error) { setForgotError(d.error); return; }
      setForgotSuccess('Code envoye ! Verifiez votre email.');
      setForgotStep(2);
    } catch { setForgotError('Erreur serveur'); }
  }

  async function doResetPwd() {
    if (!resetCode || !newPwd) { setForgotError('Code et mot de passe requis'); return; }
    setForgotError('');
    try {
      const r = await fetch('/api/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: resetCode, new_password: newPwd })
      });
      const d = await r.json();
      if (d.error) { setForgotError(d.error); return; }
      setForgotSuccess('Mot de passe modifie ! Vous pouvez vous connecter.');
      setTimeout(() => { setForgotMode(false); setForgotStep(1); setForgotError(''); setForgotSuccess(''); }, 3000);
    } catch { setForgotError('Erreur serveur'); }
  }

  return (
    <div className="lo">
      <div className="lbox">
        <div className="l-logo">
          <div className="l-icon" dangerouslySetInnerHTML={{ __html: LOGO_SVG }} />
          <div className="lwm">Guest<span style={{ color: '#4ECDC4' }}>Scale</span></div>
        </div>
        <div className="lsub">Plateforme IA pour restaurants</div>

        {!forgotMode ? (
          <form className="lcd" onSubmit={e => { e.preventDefault(); doLogin(); }} autoComplete="on">
            {error && <div className="lerr visible">{error}</div>}
            <label htmlFor="login-email" style={{ display: 'block', fontSize: 11, fontWeight: 600, color: '#6B7280', marginBottom: 4, textAlign: 'left' }}>Email</label>
            <input className="linp" id="login-email" name="email" type="email" placeholder="Email" autoComplete="email"
              style={{ marginBottom: 10 }} value={email}
              onChange={e => { setEmail(e.target.value); setError(''); }} />
            <label htmlFor="login-password" style={{ display: 'block', fontSize: 11, fontWeight: 600, color: '#6B7280', marginBottom: 4, textAlign: 'left' }}>Mot de passe</label>
            <div style={{ position: 'relative' }}>
              <input className="linp" id="login-password" name="password" ref={pwdRef} type={showPwd ? 'text' : 'password'}
                placeholder="Mot de passe" autoComplete="current-password" value={pwd}
                onChange={e => { setPwd(e.target.value); setError(''); }} />
              <button onClick={() => setShowPwd(!showPwd)}
                style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: '#6B7280', padding: 4 }}
                type="button" title="Afficher le mot de passe">
                {showPwd ? '\u{1F512}' : '\u{1F441}'}
              </button>
            </div>
            <button className="lbtn" type="submit">Se connecter</button>
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <a href="#" onClick={e => { e.preventDefault(); setForgotMode(true); setError(''); }}
                style={{ fontSize: 12, color: '#6B7280', textDecoration: 'none' }}>Mot de passe oublie ?</a>
            </div>
            <div style={{ textAlign: 'center', marginTop: 16 }}>
              <span style={{ fontSize: 12, color: '#6B7280' }}>Pas encore de compte ?</span>
              <a href="https://guestscale.com#inscription"
                style={{ fontSize: 12, color: '#4ECDC4', textDecoration: 'none', fontWeight: 600, marginLeft: 4 }}>
                Essai gratuit 30 jours
              </a>
            </div>
          </form>
        ) : (
          <div style={{ marginTop: 20 }}>
            {forgotError && <div className="lerr visible">{forgotError}</div>}
            {forgotSuccess && <div className="lerr visible success">{forgotSuccess}</div>}
            {forgotStep === 1 ? (
              <div>
                <p style={{ fontSize: 13, color: '#9CA3AF', marginBottom: 12 }}>Entrez votre email pour recevoir un code de reinitialisation.</p>
                <input className="linp" type="email" placeholder="Email" value={forgotEmail}
                  onChange={e => setForgotEmail(e.target.value)} style={{ marginBottom: 8 }} />
                <button className="lbtn" type="button" onClick={sendResetCode}>Envoyer le code</button>
              </div>
            ) : (
              <div>
                <p style={{ fontSize: 13, color: '#9CA3AF', marginBottom: 12 }}>Entrez le code recu par email et votre nouveau mot de passe.</p>
                <input className="linp" type="text" placeholder="Code a 6 chiffres" value={resetCode}
                  onChange={e => setResetCode(e.target.value)} style={{ marginBottom: 8 }} />
                <input className="linp" type="password" placeholder="Nouveau mot de passe (min. 12 car.)" value={newPwd}
                  onChange={e => setNewPwd(e.target.value)} style={{ marginBottom: 8 }} />
                <button className="lbtn" type="button" onClick={doResetPwd}>Changer le mot de passe</button>
              </div>
            )}
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <a href="#" onClick={e => { e.preventDefault(); setForgotMode(false); setForgotStep(1); setForgotError(''); setForgotSuccess(''); }}
                style={{ fontSize: 12, color: '#6B7280', textDecoration: 'none' }}>Retour a la connexion</a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
