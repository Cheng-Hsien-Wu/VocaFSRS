type MaterialSymbolName =
  | 'arrow_back'
  | 'arrow_forward'
  | 'check'
  | 'check_circle'
  | 'chevron_right'
  | 'close'
  | 'upload_file'
  | 'volume_off'
  | 'volume_up';

interface MaterialSymbolProps {
  name: MaterialSymbolName;
  className?: string;
  fill?: boolean;
}

export function MaterialSymbol({ name, className = '', fill = false }: MaterialSymbolProps) {
  return (
    <span
      className={`material-symbol ${fill ? 'material-symbol-filled' : ''} ${className}`}
      aria-hidden="true"
      translate="no"
    >
      {name}
    </span>
  );
}
