"""Judge-facing Atlas bootstrap. No downloads, credentials or visitor paths."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from tools.run_demo import RECEIPT_SHA256, unpack


def configure_public() -> Path:
    """Verify an operator-provisioned archive; never fetch substitute data."""
    archive=Path(os.environ.get('SENTINEL_ZERO_DEMO_ARCHIVE',
        str(Path(__file__).resolve().parent/'demo/scientific_data.zip')))
    cache=Path(tempfile.gettempdir())/'sz-public'/'6153417f06'
    root=unpack(archive,cache)
    os.environ['SENTINEL_ZERO_BUNDLE_ROOT']=str(root.absolute())
    os.environ['SENTINEL_ZERO_BUNDLE_SHA256']=RECEIPT_SHA256
    return root


def main() -> None:
    import streamlit as st
    try:
        configure_public()
    except (OSError,ValueError,KeyError):
        st.set_page_config(page_title='SENTINEL ZERO | Verified data required',layout='wide')
        st.title('SENTINEL ZERO 3.0')
        st.error('Verified demonstration data unavailable or inconsistent.')
        st.write('The operator must provision the approved scientific archive before this demonstration can run. No Earthdata credentials are requested and no substitute observations are shown.')
        st.stop()
    from sentinel_zero.presentation.earth_atlas import main as atlas_main
    atlas_main()


if __name__=='__main__':main()
