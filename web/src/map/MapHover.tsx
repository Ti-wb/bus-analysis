import { useCallback, useImperativeHandle, useLayoutEffect, useRef, useState, type ReactNode, type Ref, type RefObject } from 'react';
import type { MapGeoJSONFeature } from 'maplibre-gl';
import type { MapRef } from 'react-map-gl/maplibre';
import type { distancesAt } from '../coverage';

export interface Hover {
  feature?: MapGeoJSONFeature;
  distances?: NonNullable<ReturnType<typeof distancesAt>>;
  focusDistance?: number;
  x: number;
  y: number;
}

export interface MapHoverHandle {
  show: (hover: Hover | null) => void;
}

/** Pointer positions stay outside React; only changed tooltip content needs a render. */
export default function MapHover({ ref, mapRef, children }: {
  ref: Ref<MapHoverHandle>;
  mapRef: RefObject<MapRef | null>;
  children: (hover: Hover) => ReactNode;
}) {
  const [hover, setHover] = useState<Hover | null>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const point = useRef({ x: 0, y: 0 });
  const contentKey = useRef('');
  const size = useRef({ width: 0, height: 0, mapWidth: 0, mapHeight: 0 });
  const position = useCallback(() => {
    const { width, height, mapWidth, mapHeight } = size.current;
    const { x, y } = point.current;
    const left = Math.max(0, x + 12 + width <= mapWidth ? x + 12 : x - 12 - width);
    const top = Math.max(0, y + 12 + height <= mapHeight ? y + 12 : y - 12 - height);
    if (tipRef.current) tipRef.current.style.transform = `translate3d(${left}px, ${top}px, 0)`;
  }, []);

  useImperativeHandle(ref, () => ({ show(next) {
    point.current = next ?? point.current;
    const key = next?.feature ? `${next.feature.layer.id}:${next.feature.id}:${JSON.stringify(next.feature.properties)}`
      : next?.distances ? JSON.stringify(next.distances)
      : next?.focusDistance !== undefined ? `distance:${Math.round(next.focusDistance)}` : '';
    if (key !== contentKey.current) { contentKey.current = key; setHover(next); }
    position();
  } }), [position]);

  useLayoutEffect(() => {
    const tip = tipRef.current, container = mapRef.current?.getContainer();
    if (!tip || !container) return;
    const measure = () => {
      size.current = { width: tip.offsetWidth, height: tip.offsetHeight, mapWidth: container.clientWidth, mapHeight: container.clientHeight };
      position();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(tip); observer.observe(container);
    return () => observer.disconnect();
  }, [hover !== null, mapRef, position]);

  return hover && <div ref={tipRef} className="tooltip" style={{ left: 0, top: 0 }}>{children(hover)}</div>;
}
