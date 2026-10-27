import React from 'react';

interface NetRescueLogoProps {
    size?: number;
    className?: string;
}

export default function NetRescueLogo({ size = 18, className = '' }: NetRescueLogoProps) {
    return (
        <svg
            width={size}
            height={size}
            viewBox="-50 -50 100 100"
            fill="currentColor"
            className={className}
            aria-hidden="true"
        >
            {[0, 90, 180, 270].map(angle => (
                <path
                    key={angle}
                    transform={`rotate(${angle})`}
                    d="M 2,22 L 2,32 C 2,37 6.5,41.5 11.5,41.5 L 16.5,41.5 C 21.5,41.5 26,37 26,32 Q 27.5,27 32,25.5 C 37,25.5 41.5,21 41.5,16 L 41.5,11.5 C 41.5,6.5 37,2 32,2 L 22,2 Q 12,12 2,22 Z"
                />
            ))}
        </svg>
    );
}
