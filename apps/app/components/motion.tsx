"use client";
// El markup que sirve el servidor ya es el estado final de la grafica: barras a
// su anchura real y lineas dibujadas. No es un detalle de estilo: es lo que hace
// que la grafica se lea sin JS y lo que mantiene ciertos los asserts estaticos
// de scripts/check-web.ts. Por eso las animaciones de entrada solo corren en
// cliente, una vez montado el componente, y nunca sobre atributos leidos por
// los tests (width, d, y...): se usa transform y opacity.
import { domAnimation, LazyMotion, MotionConfig, useReducedMotion, m } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";

// Entrada suave para trazos y barras.
export const enterTransition = { duration: 0.6, ease: [0.22, 1, 0.36, 1] } as const;
// Muelle corto para la respuesta al puntero.
export const pressSpring = { type: "spring", stiffness: 500, damping: 32 } as const;

// Proveedor obligatorio para cualquier componente m.* de estas graficas:
// LazyMotion con domAnimation mantiene el bundle ligero y, con strict, falla
// ruidosamente si alguien importa el motion completo; reducedMotion="user"
// desactiva toda animacion cuando el sistema pide menos movimiento.
export function ChartMotion({ children }: { children: ReactNode }) {
  return <LazyMotion features={domAnimation} strict><MotionConfig reducedMotion="user">{children}</MotionConfig></LazyMotion>;
}

// Falso con prefers-reduced-motion: reduce y falso hasta el montaje, para que
// el primer render (incluido el SSR) sea identico al markup estatico final.
export function useChartMotion() {
  const reducedMotion = useReducedMotion();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted && !reducedMotion;
}

// Relleno de barra que crece desde su origen izquierdo con scaleX, una sola vez
// al montar. La anchura real va siempre en style.width (la leen los tests); la
// animacion solo toca el transform, y sin JS el elemento se serve ya lleno.
export function GrowingBar({ as = "i", width }: { as?: "i" | "span"; width: string }) {
  const animate = useChartMotion();
  if (!animate) return as === "i" ? <i style={{ width }}/> : <span style={{ width }}/>;
  const Tag = m[as];
  return <Tag style={{ width, transformOrigin: "0% 50%" }} initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={enterTransition}/>;
}
