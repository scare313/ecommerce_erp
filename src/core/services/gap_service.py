import pandas as pd
from src.infrastructure.database import get_engine
from src.infrastructure.logger import get_logger, DatabaseException, ServiceException

logger = get_logger(__name__)

class GapService:
    """Service for analyzing gaps between ideal and actual catalog listings."""
    
    def __init__(self):
        """Initialize gap service with database engine."""
        try:
            self.engine = get_engine()
            logger.debug("GapService initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize GapService: {str(e)}", exc_info=True)
            raise

    def get_gap_matrix(self):
        """
        Generate a gap analysis matrix comparing ideal vs actual catalog state.
        
        Returns:
            DataFrame: Gap matrix with columns:
                - pack_sku: Sellable pack identifier
                - master_sku: Base product identifier  
                - marketplace: Channel name
                - listing_count: Number of actual listings (0 if gap)
                
        Raises:
            DatabaseException: If database queries fail
            ServiceException: If data processing fails
        """
        try:
            logger.info("Generating gap analysis matrix...")
            
            try:
                # 1. Get all Sellable Packs (Rows)
                logger.debug("Fetching pack master data...")
                packs = pd.read_sql("SELECT pack_sku, master_sku FROM pack_master", self.engine)
                logger.debug(f"Retrieved {len(packs)} packs")

                # 2. Get all Active Marketplaces (Columns)
                logger.debug("Fetching marketplace configuration...")
                markets = pd.read_sql("SELECT marketplace FROM config", self.engine)
                if markets.empty:
                    logger.warning("No marketplaces configured, using defaults")
                    market_list = ["Amazon", "Flipkart", "Meesho"]
                else:
                    market_list = markets['marketplace'].tolist()
                logger.debug(f"Using {len(market_list)} marketplaces: {market_list}")

                # 3. Create the "Ideal State" (Cartesian Product)
                logger.debug("Creating ideal catalog matrix...")
                packs['key'] = 1
                markets_df = pd.DataFrame({'marketplace': market_list, 'key': 1})
                ideal_catalog = pd.merge(packs, markets_df, on='key').drop('key', axis=1)
                logger.debug(f"Ideal catalog size: {len(ideal_catalog)} combinations")

                # 4. Get Actual Listings
                logger.debug("Fetching actual channel listings...")
                listings = pd.read_sql(
                    "SELECT internal_sku, marketplace, listing_status FROM channel_listings", 
                    self.engine
                )
                logger.debug(f"Retrieved {len(listings)} listings")

                # 5. AGGREGATION: Count listings per Pack+Market
                logger.debug("Aggregating listings...")
                if listings.empty:
                    logger.warning("No listings found in database")
                    grouped = pd.DataFrame(columns=['internal_sku', 'marketplace', 'listing_count'])
                else:
                    grouped = listings.groupby(['internal_sku', 'marketplace']).agg(
                        listing_count=('internal_sku', 'count')
                    ).reset_index()
                logger.debug(f"Created {len(grouped)} listing groups")

                # 6. Merge Ideal vs Actual
                logger.debug("Merging ideal and actual states...")
                merged = pd.merge(
                    ideal_catalog, 
                    grouped, 
                    left_on=['pack_sku', 'marketplace'], 
                    right_on=['internal_sku', 'marketplace'], 
                    how='left'
                )
                
                # 7. Fill Gaps
                merged['listing_count'] = merged['listing_count'].fillna(0).astype(int)
                
                # Calculate gap metrics
                gaps = merged[merged['listing_count'] == 0]
                gap_count = len(gaps)
                gap_percentage = (gap_count / len(merged) * 100) if len(merged) > 0 else 0
                
                logger.info(f"✅ Gap analysis complete: {gap_count} gaps out of {len(merged)} combinations ({gap_percentage:.1f}%)")
                logger.debug(f"Sample gaps: {gaps[['pack_sku', 'marketplace']].head().to_dict('records')}")
                
                return merged
                
            except Exception as e:
                logger.error(f"Error during gap analysis processing: {str(e)}", exc_info=True)
                raise ServiceException(f"Gap analysis failed: {str(e)}") from e
                
        except ServiceException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in gap matrix generation: {str(e)}", exc_info=True)
            raise